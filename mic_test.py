import sounddevice as sd
import numpy as np
import argparse
import time
import sys
import pyttsx3 # ADDED FOR DEBUGGING
from comtypes import CLSCTX_ALL # ADDED FOR DEBUGGING
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume # ADDED FOR DEBUGGING

def list_devices():
    """Prints available audio input devices."""
    print("--- Available Audio Input Devices ---")
    try:
        devices = sd.query_devices()
        input_devices_found = False
        for i, device in enumerate(devices):
            # Check if it's an input device with a valid name
            if device['max_input_channels'] > 0 and device['name']:
                print(f"  Index {i}: {device['name']}")
                input_devices_found = True
        if not input_devices_found:
            print("No audio input devices found.")
        print("-------------------------------------\n")
    except Exception as e:
        print(f"Could not list audio devices: {e}")

def test_microphone(device_index):
    """Tests a specific microphone device and prints its RMS value."""
    # --- Initialize other libraries to test for conflict ---
    print("Initializing text-to-speech engine to check for conflicts...")
    try:
        engine = pyttsx3.init()
        print("Text-to-speech engine initialized.")
    except Exception as e:
        print(f"Could not initialize pyttsx3: {e}")
    
    print("Initializing volume controller to check for conflicts...")
    try:
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = interface.QueryInterface(IAudioEndpointVolume)
        print("Volume controller initialized.")
    except Exception as e:
        print(f"Could not initialize pycaw: {e}")
    # ----------------------------------------------------

    if device_index is None:
        print("\n--- Testing Default System Device ---")
    else:
        print(f"\n--- Testing Device Index: {device_index} ---")
    
    samplerate = 16000
    blocksize = int(samplerate * 0.2) # 200ms chunks for smoother reading

    try:
        def callback(indata, frames, time, status):
            """This is called for each audio chunk."""
            if status:
                print(status, file=sys.stderr)
            # Calculate RMS and scale it up for better visibility
            rms = np.sqrt(np.mean(indata**2))
            # Use carriage return to print on the same line
            print(f"RMS: {int(rms * 1000):<5}", end='\r')

        with sd.InputStream(device=device_index, channels=1, samplerate=samplerate, blocksize=blocksize, callback=callback):
            print("Printing RMS value. Speak into your microphone now!")
            print("The RMS value should increase when you talk.")
            # Keep the stream open for the user to test
            for i in range(20): # Run for 20 seconds
                time.sleep(1)
            print("\nTest finished.")

    except Exception as e:
        print(f"\nError: Could not open device {device_index}. It may be invalid or in use.")
        print(f"  Details: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test microphone input level (RMS).")
    parser.add_argument(
        "-l", "--list", 
        action="store_true", 
        help="List available audio devices and exit."
    )
    parser.add_argument(
        "-d", "--device", 
        type=int,
        help="The index of the audio device to test."
    )
    
    args = parser.parse_args()

    if args.list:
        list_devices()
    elif args.device is not None:
        test_microphone(args.device)
    else:
        # If no arguments are given, list devices and provide instructions.
        list_devices()
        print("Usage: Run this script with the --device flag to test a microphone.")
        print("Example: python mic_test.py --device 1")
