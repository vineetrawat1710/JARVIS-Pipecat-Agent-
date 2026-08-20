# Professional listening module for Jarvis AI Assistant
# Uses a self-downloading, offline-first Silero VAD model for robust voice activity detection.

import configparser
import whisper
import numpy as np
import sounddevice as sd
import torch
import queue
import threading

# --- Silero VAD ---
VAD_MODEL = None

# --- Global Settings ---
DEVICE_INDEX = None
WHISPER_MODEL = None
SAMPLERATE = 16000  # Silero VAD and Whisper require 16000Hz

VAD_THRESHOLD = 0.5
VAD_TRIGGER_TIME_S = 0.2
VAD_RELEASE_TIME_S = 1.0

# The VAD processes audio in chunks.
# It must be one of: 256, 512, 768, 1024, 1536
VAD_CHUNK_SIZE = 512

# --- Other Globals ---
audio_queue = queue.Queue()
recording_state = "IDLE"  # Can be IDLE, LISTENING, RECORDING, TRANSCRIBING


def init(config: configparser.ConfigParser):
    """
    Initializes the listening module: loads models and sets audio configuration.
    Will download the VAD model if it's not found locally.
    """
    global DEVICE_INDEX, VAD_THRESHOLD, VAD_TRIGGER_TIME_S, VAD_RELEASE_TIME_S
    global WHISPER_MODEL, VAD_MODEL

    # --- Load Audio Device Configuration ---
    try:
        DEVICE_INDEX_STR = config.get('Audio', 'device_index', fallback='auto')
        DEVICE_INDEX = None if DEVICE_INDEX_STR.lower() == 'auto' else int(DEVICE_INDEX_STR)
        print(f"Audio config: Using microphone index {DEVICE_INDEX or 'Default'}.")
    except (configparser.NoSectionError, ValueError) as e:
        print(f"Config error reading device_index: {e}. Using default.")
        DEVICE_INDEX = None

    # --- Load VAD Configuration ---
    try:
        VAD_THRESHOLD = config.getfloat('Audio', 'vad_threshold', fallback=0.5)
        VAD_TRIGGER_TIME_S = config.getfloat('Audio', 'vad_trigger_time', fallback=0.2)
        VAD_RELEASE_TIME_S = config.getfloat('Audio', 'vad_release_time', fallback=1.0)
    except (configparser.NoSectionError, ValueError) as e:
        print(f"Config error in [Audio] VAD settings: {e}. Using default values.")

    # --- Load AI Models ---
    if WHISPER_MODEL is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Listening module: Using {device} for transcription.")

        model_name = config.get('General', 'whisper_model', fallback='small.en')

        if device == "cpu" and model_name not in ["tiny.en", "base.en", "small.en"]:
            print(
                f"Warning: Whisper model '{model_name}' is not recommended for CPU. "
                f"Switching to 'small.en' for better performance."
            )
            model_name = "small.en"

        print(f"Listening module: Loading Whisper '{model_name}' model...")
        WHISPER_MODEL = whisper.load_model(model_name, device=device)
        print("Listening module: Whisper model loaded.")

    if VAD_MODEL is None:
        print("Listening module: Loading Silero VAD model...")
        try:
            VAD_MODEL, _ = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                force_reload=False,
                onnx=False
            )
            VAD_MODEL.eval()
            print("Listening module: Silero VAD model loaded successfully.")
        except Exception as e:
            print(f"FATAL: Failed to load VAD model: {e}")
            VAD_MODEL = None


def _audio_callback(indata, frames, time, status):
    """This is called by sounddevice for each audio chunk."""
    if status:
        print(f"Audio stream warning: {status}", flush=True)
    audio_queue.put(indata.copy())


def listen():
    """
    Listens for speech using Silero VAD, records it, and returns the transcribed text.
    Manages a state machine for robust, intelligent speech detection.
    """
    global recording_state

    if VAD_MODEL is None:
        print("VAD model not loaded. Cannot listen.")
        return "Error: VAD model is not loaded."

    recording_state = "LISTENING"

    trigger_chunks = int(VAD_TRIGGER_TIME_S / (VAD_CHUNK_SIZE / SAMPLERATE))
    release_chunks = int(VAD_RELEASE_TIME_S / (VAD_CHUNK_SIZE / SAMPLERATE))

    recorded_audio = []
    speech_chunks_count = 0
    silence_chunks_count = 0

    try:
        stream = sd.InputStream(
            samplerate=SAMPLERATE,
            channels=1,
            dtype='float32',
            callback=_audio_callback,
            device=DEVICE_INDEX,
            blocksize=VAD_CHUNK_SIZE
        )
    except Exception as e:
        print(f"FATAL: Error opening audio stream: {e}")
        return "Error: Could not open microphone. Check device_index in config.ini."

    stream.start()
    print("Listening for speech...")

    # Clear stale audio
    while not audio_queue.empty():
        audio_queue.get()

    while recording_state != "TRANSCRIBING":
        chunk_numpy = audio_queue.get()

        audio_level = np.abs(chunk_numpy).max()
        chunk_tensor = torch.from_numpy(chunk_numpy).flatten()
        speech_prob = VAD_MODEL(chunk_tensor, SAMPLERATE).item()

        print(
            f"VAD Speech Probability: {speech_prob:.2f} | Audio Level: {audio_level:.4f}",
            end='\r'
        )

        if speech_prob > VAD_THRESHOLD:
            if recording_state == "LISTENING":
                speech_chunks_count += 1
                if speech_chunks_count > trigger_chunks:
                    print("\nSpeech detected, recording...")
                    recording_state = "RECORDING"
                    recorded_audio.append(chunk_numpy)
            elif recording_state == "RECORDING":
                recorded_audio.append(chunk_numpy)
                silence_chunks_count = 0
        else:
            if recording_state == "RECORDING":
                silence_chunks_count += 1
                if silence_chunks_count > release_chunks:
                    print("End of speech detected.")
                    recording_state = "TRANSCRIBING"
                else:
                    recorded_audio.append(chunk_numpy)
            elif recording_state == "LISTENING":
                speech_chunks_count = 0

    stream.stop()
    stream.close()

    if not recorded_audio:
        print("No audio recorded.")
        return ""

    full_recording = np.concatenate(recorded_audio, axis=0).flatten()
    print("Transcribing audio...")

    try:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        use_fp16 = (device == "cuda")
        result = WHISPER_MODEL.transcribe(
            full_recording,
            fp16=use_fp16,
            language="en"
        )
        transcribed_text = result['text'].strip()
        print(f"Transcribed text: {transcribed_text}")
    except Exception as e:
        print(f"Error during transcription: {e}")
        transcribed_text = ""

    return transcribed_text


def list_audio_devices():
    """Prints a list of available audio input devices."""
    print("\n--- Available Audio Input Devices ---")
    try:
        devices = sd.query_devices()
        input_devices = [
            (i, d['name'])
            for i, d in enumerate(devices)
            if d['max_input_channels'] > 0
        ]

        if not input_devices:
            print("No audio input devices found.")

        for i, name in input_devices:
            print(f"  Index {i}: {name}")

        print("-------------------------------------\n")
    except Exception as e:
        print(f"Could not list audio devices: {e}")


if __name__ == '__main__':
    print("This module is not meant to be run directly. Use 'jarvis.py'.")
    list_audio_devices()
