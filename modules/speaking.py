import pyttsx3
import pythoncom

def say(text: str):
    if not text:
        return

    pythoncom.CoInitialize()
    
    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", 180)
        print(f"Jarvis says: {text}")
        engine.say(text)
        engine.runAndWait()
        engine.stop()

    finally:
        pythoncom.CoUninitialize()
