"""
Jarvis AI Assistant — Pipecat 1.2.1 Pipeline
Stack: Silero VAD → Deepgram STT → Groq LLM (llama-3.3-70b) → Kokoro TTS (ONNX)
"""

import asyncio
import configparser
import sys
import json
import time
import numpy as np
import sounddevice as sd
from loguru import logger
from faster_whisper import WhisperModel

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    Frame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    STTMuteFrame,
    TTSSpeakFrame,
    VADUserStartedSpeakingFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Existing desktop tools
from modules.actions import AVAILABLE_TOOLS_LLM_JSON, TOOL_FUNCTIONS

# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are Jarvis, a witty, highly capable AI desktop assistant inspired by Jarvis from Iron Man. The user's name is Vineet.
You are helpful, slightly sarcastic, and always efficient. You can understand English and Hinglish.

INTENT-FIRST RULES:
1. Always analyze the user's intent to identify what they want to achieve.
2. For ALL web tasks (such as searching Google, searching YouTube, opening websites, playing videos, or browsing social media), use the `open_url` tool.
   - You must construct the exact destination URL yourself based on the user's query.
   - For YouTube searches, use: https://www.youtube.com/results?search_query=YOUR_SEARCH_QUERY
   - For Google searches, use: https://www.google.com/search?q=YOUR_SEARCH_QUERY
   - To open any website directly, use that website's home page.
3. ALWAYS prioritize using specific built-in tools (like `set_volume`, `set_brightness`, `open_application`) over the generic `run_system_command` tool.
4. ONLY use `run_system_command` as a last resort for tasks that don't have a dedicated tool (e.g., executing scripts, managing unhandled system settings).
5. When initiating a tool call, output ONLY the JSON tool call payload. Once the tool runs and returns its result, verbally confirm the completion to the user in 1 short sentence (e.g. "I've set the brightness to 50%").
6. Keep ALL verbal responses to 1-2 short sentences. Speak naturally.
7. NEVER output XML tags, raw JSON, function/tool name strings (like '<vineet>', 'open_url', etc.) in your speech. Speak like a human. If a command fails, just say so simply.
8. If the user's input is a short, incomplete fragment (like "I said", "hello", "no"), just reply conversationally (e.g. "Yes, Vineet?" or "Go ahead") rather than calling tools or generating tags.
9. If the user says goodbye, bye, or ends the conversation, simply reply with a polite farewell (e.g. "Goodbye, Vineet!"). DO NOT call any tools.
"""

GREETING = "Jarvis online. Ready when you are, Vineet."
# ─────────────────────────────────────────────────────────────────────────────

# Global state to trigger sleep transition after bot finishes speaking
should_sleep_after_speaking = False


class SanitizedLLMContext(LLMContext):
    """Subclass of LLMContext that ensures tool_calls arguments are always valid JSON strings and prunes context size."""
    def get_messages(self, *args, **kwargs):
        messages = super().get_messages(*args, **kwargs)
        
        # Keep system prompt + last 8 messages (approx 4 conversation turns) to avoid context bloating
        if len(messages) > 9:
            system_msg = messages[0]
            recent_msgs = messages[-8:]
            
            # Ensure we start with a 'user' message to keep context history structurally valid for Groq
            start_idx = 0
            while start_idx < len(recent_msgs) and recent_msgs[start_idx].get("role") != "user":
                start_idx += 1
            
            messages = [system_msg] + recent_msgs[start_idx:]
            
        for msg in messages:
            if isinstance(msg, dict) and "tool_calls" in msg:
                for tool_call in msg["tool_calls"]:
                    func = tool_call.get("function", {})
                    args_val = func.get("arguments")
                    if args_val is None or args_val == "null" or args_val == "None":
                        func["arguments"] = "{}"
                    elif not isinstance(args_val, str):
                        try:
                            func["arguments"] = json.dumps(args_val)
                        except Exception:
                            func["arguments"] = "{}"
        return messages


class SleepTriggerFilter(FrameProcessor):
    """Pipeline processor that detects goodbye/sleep commands to put Jarvis back to sleep."""
    def __init__(self, sleep_words=None):
        super().__init__()
        self.sleep_words = [w.lower() for w in (sleep_words or ["bye", "goodbye", "go to sleep", "sleep now"])]

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)   # ← required: lets base class handle StartFrame
        global should_sleep_after_speaking
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TranscriptionFrame):
            text = frame.text.lower()
            if any(w in text for w in self.sleep_words):
                logger.info("💤 Sleep word detected. Jarvis will enter sleep mode after this response.")
                should_sleep_after_speaking = True
        await self.push_frame(frame, direction)


def listen_for_wake_word(model):
    """Listens for the wake word offline using VAD-gated Whisper — only clean speech segments are transcribed."""
    import threading

    vad = SileroVADAnalyzer(params=VADParams(
        confidence=0.75,
        start_secs=0.2,
        stop_secs=0.5,
        min_volume=0.3,
    ))
    vad.set_sample_rate(16000)

    sample_rate = 16000
    chunk_size  = 512  # Silero VAD requires exactly 512 samples at 16kHz

    WAKE_WORDS = [
        "jarvis", "jarves", "jarv", "javis", "jardin",
        "hey", "hello", "buddy", "wake",
    ]

    logger.info("Listening for 'Jarvis' offline (0 API cost)...")

    while True:
        audio_chunks   = []
        is_speaking    = False
        silence_count  = 0
        max_silence    = int(0.8 * sample_rate / chunk_size)   # ~0.8 s of silence ends a phrase
        done_event     = threading.Event()

        def callback(indata, frames, time_info, status):
            nonlocal is_speaking, silence_count
            audio_bytes  = (indata[:, 0] * 32768).astype(np.int16).tobytes()
            confidence   = vad.voice_confidence(audio_bytes)

            if confidence > 0.75:
                is_speaking    = True
                silence_count  = 0
                audio_chunks.append(indata[:, 0].copy())
            elif is_speaking:
                audio_chunks.append(indata[:, 0].copy())
                silence_count += 1
                if silence_count >= max_silence:
                    done_event.set()   # signal main thread that phrase ended

        try:
            with sd.InputStream(samplerate=sample_rate, channels=1,
                                callback=callback, blocksize=chunk_size):
                # wait until VAD signals end-of-phrase or 8 seconds max
                triggered = done_event.wait(timeout=8.0)
        except Exception as e:
            logger.error(f"Mic error: {e}")
            time.sleep(0.5)
            continue

        if not audio_chunks:
            continue   # pure silence — loop again

        audio_data = np.concatenate(audio_chunks).astype(np.float32)
        segments, _ = model.transcribe(audio_data, beam_size=1, language="en")
        text = "".join(seg.text for seg in segments).strip().lower()

        if text:
            logger.info(f"Offline Transcribed: '{text}'")

        if any(w in text for w in WAKE_WORDS):
            logger.info("⏰ Wake word matched — starting Jarvis!")
            break


async def run_pipecat_pipeline(groq_api_key, groq_model, deepgram_api_key):
    global should_sleep_after_speaking
    
    # ── Transport: Mic + Speaker ──────────────────────────────────────────────
    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        )
    )

    # ── VAD: Voice Activity Detection (tuned to ignore background noise) ────────
    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(
            params=VADParams(
                confidence=0.85,
                start_secs=0.5,
                stop_secs=0.4,
                min_volume=0.75,
            )
        )
    )

    # ── STT: Deepgram — ultra-low latency streaming transcription ───────────────
    stt = DeepgramSTTService(
        api_key=deepgram_api_key,
        settings=DeepgramSTTService.Settings(
            model="nova-3-general",
            smart_format=True,
            punctuate=True,
            interim_results=False,
        ),
    )

    # ── LLM: Groq — Ultra-fast inference ───────────────
    llm = OpenAILLMService(
        api_key=groq_api_key,
        base_url="https://api.groq.com/openai/v1",
        settings=OpenAILLMService.Settings(model=groq_model),
    )

    # ── TTS: Kokoro — British male voice (classic Jarvis) ───────────────────────
    tts = KokoroTTSService(
        settings=KokoroTTSService.Settings(voice="bm_fable")
    )

    # ── Convert actions.py metadata into Pipecat FunctionSchema ───────────────
    tools = []
    for tool_def in AVAILABLE_TOOLS_LLM_JSON:
        f = tool_def["function"]
        tools.append(
            FunctionSchema(
                name=f["name"],
                description=f["description"],
                properties=f["parameters"]["properties"],
                required=f["parameters"].get("required", []),
            )
        )
    tools_schema = ToolsSchema(standard_tools=tools)

    # ── Context: system prompt + tool definitions ─────────────────────────────
    context = SanitizedLLMContext(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}],
        tools=tools_schema,
    )
    user_agg, assistant_agg = LLMContextAggregatorPair(context)

    # ── Register tool call handlers ───────────────────────────────────────────
    for func in TOOL_FUNCTIONS:
        def make_handler(f):
            async def handler(params):
                logger.info(f"🔧 Tool call → {params.function_name}({params.arguments})")
                try:
                    args = params.arguments if isinstance(params.arguments, dict) else {}
                    
                    # Filter arguments to match the function signature and avoid crashes from LLM hallucinations
                    import inspect
                    sig = inspect.signature(f)
                    has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                    if not has_kwargs:
                        valid_keys = set(sig.parameters.keys())
                        args = {k: v for k, v in args.items() if k in valid_keys}
                        
                    result = await asyncio.to_thread(f, **args)
                except Exception as e:
                    result = f"Error executing tool: {e}"
                logger.info(f"   ✅ {result}")
                await params.result_callback(result)
            return handler

        llm.register_function(func.__name__, make_handler(func))

    # ── Sleep filter ──────────────────────────────────────────────────────────
    sleep_filter = SleepTriggerFilter()

    # ── Build the pipeline ────────────────────────────────────────────────────
    pipeline = Pipeline([
        transport.input(),
        vad,
        stt,
        sleep_filter,
        user_agg,
        llm,
        tts,
        transport.output(),
        assistant_agg,
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
        ),
    )

    # ── Greet the user on startup ─────────────────────────────────────────────
    @task.event_handler("on_pipeline_started")
    async def on_pipeline_started(task, frame):
        await task.queue_frame(TTSSpeakFrame(GREETING))

    # ── Echo suppression: mute STT while Jarvis is speaking ───────────────────
    task.set_reached_downstream_filter((
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        VADUserStartedSpeakingFrame,
    ))

    runner = PipelineRunner()

    @task.event_handler("on_frame_reached_downstream")
    async def on_frame_downstream(task, frame):
        global should_sleep_after_speaking
        if isinstance(frame, BotStartedSpeakingFrame):
            await task.queue_frame(STTMuteFrame(mute=True))
        elif isinstance(frame, (BotStoppedSpeakingFrame, VADUserStartedSpeakingFrame)):
            await task.queue_frame(STTMuteFrame(mute=False))
            if isinstance(frame, BotStoppedSpeakingFrame) and should_sleep_after_speaking:
                logger.info("💤 User requested sleep. Exiting active pipeline...")
                should_sleep_after_speaking = False
                await runner.cancel()

    await runner.run(task)


async def main():
    # ── Load config ───────────────────────────────────────────────────────────
    config = configparser.ConfigParser()
    config.read("config.ini")

    groq_api_key = config.get("APIs", "Groq_api_key", fallback="")
    if not groq_api_key or "YOUR" in groq_api_key:
        logger.error("Missing Groq API key in config.ini!")
        sys.exit(1)

    groq_model = config.get("AI", "groq_model", fallback="llama-3.3-70b-versatile")

    deepgram_api_key = config.get("APIs", "Deepgram_api_key", fallback="")
    if not deepgram_api_key or "YOUR" in deepgram_api_key:
        logger.error("Missing Deepgram API key in config.ini!")
        sys.exit(1)

    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"  🎙️  STT (Offline Wake) : Whisper tiny (CPU)")
    logger.info(f"  🎙️  STT (Active Session) : Deepgram (nova-3-general)")
    logger.info(f"  🧠  LLM : Groq ({groq_model})")
    logger.info(f"  🔊  TTS : Kokoro (bm_fable)")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Load tiny whisper model on CPU once on startup
    logger.info("Initializing offline wake-word detector model...")
    wake_model = WhisperModel("tiny", device="cpu", compute_type="int8")
    logger.info("Offline wake-word detector model ready.")

    while True:
        # Step 1: Wait for wake word offline (free)
        await asyncio.to_thread(listen_for_wake_word, wake_model)
        
        # Step 2: Check internet connection
        import socket
        def is_connected():
            try:
                socket.create_connection(("1.1.1.1", 53), timeout=2)
                return True
            except OSError:
                return False

        logger.info("⏰ Waking up Jarvis! Starting interactive session...")
        
        if not is_connected():
            logger.warning("No internet connection. Playing offline warning.")
            import pyttsx3
            try:
                engine = pyttsx3.init()
                engine.setProperty("rate", 180)
                engine.say("I am currently offline. Please check your internet connection.")
                engine.runAndWait()
            except Exception as e:
                logger.error(f"Failed to play offline warning: {e}")
        else:
            try:
                await run_pipecat_pipeline(groq_api_key, groq_model, deepgram_api_key)
            except Exception as e:
                logger.error(f"Error during active session: {e}")
                import pyttsx3
                try:
                    engine = pyttsx3.init()
                    engine.setProperty("rate", 180)
                    engine.say("I encountered a network error while connecting to my servers.")
                    engine.runAndWait()
                except Exception:
                    pass
        
        logger.info("💤 Jarvis is sleeping. Returning to offline monitoring...")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Goodbye, Vineet.")
