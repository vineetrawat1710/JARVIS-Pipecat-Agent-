import os
import webbrowser
import configparser
from datetime import datetime
import requests
from comtypes import CLSCTX_ALL
from ctypes import cast, POINTER
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
import screen_brightness_control as sbc
import inspect
import yaml
import pyjokes
import certifi
import subprocess
import shutil  
conversation_history = []
MAX_HISTORY_PAIRS = 5


# -----------------------------------------------------------------------------
# region Individual Tool Functions
# Each function designed to be a "tool" for the AI must have a docstring
# containing a 'tool_schema' block in YAML format.
# -----------------------------------------------------------------------------

def create_folder(folder_name: str):
    """
    Creates a new folder with the given name.

    tool_schema:
      properties:
        folder_name:
          type: string
          description: "The absolute or relative path and name of the folder to create."
      required: ["folder_name"]
    """
    if not folder_name:
        return "I'm sorry, I didn't catch the folder name. Please try again."
    
    sanitized_folder_name = os.path.abspath(os.path.normpath(folder_name))
    try:
        os.makedirs(sanitized_folder_name, exist_ok=True)
        success_message = f"Successfully created folder: {folder_name}"
        print(success_message)
        return success_message
    except OSError as e:
        error_message = f"Error creating folder '{folder_name}': {e}"
        print(error_message)
        return f"Sorry, I encountered an error and couldn't create the folder {folder_name}."

def open_url(url: str):
    """
    Opens any URL in the default web browser. Use this for all web-based tasks like opening websites, searching Google, or searching YouTube. Construct the correct URL based on the user's intent.

    tool_schema:
      properties:
        url:
          type: string
          description: "The full URL starting with https:// to open in the browser."
      required: ["url"]
    """
    if not url or not url.startswith("http"):
        return "I need a valid URL to open. Please specify what you want to open."
    import urllib.parse
    webbrowser.open(url)
    message = f"Opening {url} in your browser."
    print(message)
    return message

def open_application(app_name: str):
    """
    Opens a specified application from a predefined list in the config file.

    tool_schema:
      properties:
        app_name:
          type: string
          description: "The name of the application to open, e.g., notepad, chrome."
      required: ["app_name"]
    """
    if not app_name:
        return "I'm sorry, I didn't catch the application name."
    app_name = app_name.lower().strip()
    exe_name = app_name if app_name.endswith(".exe") else f"{app_name}.exe"

    # 🔹 Try using Windows shell start command
    path = shutil.which(app_name) or shutil.which(exe_name)
    if path:
        subprocess.Popen(path)
        return f"Opening {app_name}."

    # 2️⃣ Search common install locations
    search_roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LocalAppData"),
    ]

    for root_dir in search_roots:
        if not root_dir:
            continue

        for root, dirs, files in os.walk(root_dir):
            if exe_name in files:
                full_path = os.path.join(root, exe_name)
                subprocess.Popen(full_path)
                return f"Opening {app_name}."

    return f"Sorry, I couldn't find an application named {app_name}."

def create_file(file_path: str):
    """
    Creates a new empty file at the given path.

    tool_schema:
      properties:
        file_path:
          type: string
          description: "The absolute or relative path for the new file, including the filename and extension."
      required: ["file_path"]
    """
    if not file_path:
        return "I'm sorry, I didn't get the file name or path. Please try again."
    
    sanitized_file_path = os.path.abspath(os.path.normpath(file_path))
    try:
        os.makedirs(os.path.dirname(sanitized_file_path), exist_ok=True)
        with open(sanitized_file_path, 'w') as f:
            pass
        message = f"Successfully created file at {file_path}"
        print(message)
        return message
    except Exception as e:
        error_message = f"Sorry, I encountered an error creating the file: {e}"
        print(error_message)
        return error_message

def get_time():
    """
    Tells the current time.

    tool_schema:
      properties: {}
    """
    now = datetime.now()
    current_time = now.strftime("%I:%M %p")
    message = f"The current time is {current_time}."
    print(message)
    return message

def tell_joke():
    """
    Tells a random programmer joke.

    tool_schema:
      properties: {}
    """
    joke = pyjokes.get_joke()
    print(f"Joke: {joke}")
    return joke

def get_weather(city: str):
    """
    Gets the current weather for a specified city using OpenWeatherMap API.

    tool_schema:
      properties:
        city:
          type: string
          description: "The city name for which to get the weather, e.g., London."
      required: ["city"]
    """
    if not city:
        return "I'm sorry, I didn't catch the city name."

    config = configparser.ConfigParser()
    config.read('config.ini')
    api_key = config.get('APIs', 'OpenWeatherMap_key', fallback=None)

    if not api_key or api_key == 'YOUR_API_KEY_HERE':
        return "Weather service is not configured. Please add an OpenWeatherMap API key to your config.ini file."

    base_url = "http://api.openweathermap.org/data/2.5/weather?"
    complete_url = f"{base_url}appid={api_key}&q={city}&units=metric"
    response = requests.get(complete_url)
    data = response.json()

    if data["cod"] != "404":
        main = data["main"]
        weather_desc = data["weather"][0]["description"]
        temp = main["temp"]
        return f"The weather in {city} is currently {weather_desc} with a temperature of {temp} degrees Celsius."
    else:
        return f"Sorry, I couldn't find the weather for {city}."

def set_volume(level: int):
    """
    Sets the system volume to a specific level (0-100).

    tool_schema:
      properties:
        level:
          type: integer
          description: "The desired volume level, from 0 to 100."
      required: ["level"]
    """
    if level is None:
        return "I'm sorry, I didn't catch the desired volume level."
    
    try:
        level = int(level)
        if not (0 <= level <= 100):
            return "Please specify a volume level between 0 and 100."

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        interface = cast(interface, POINTER(IAudioEndpointVolume))
        interface.SetMasterVolumeLevelScalar(level / 100, None)
        msg = f"Volume set to {level}%."
        print(msg)
        return msg
    except Exception as e:
        error_msg = f"Error setting volume: {e}"
        print(error_msg)
        return "Sorry, I couldn't change the volume."

def volume_up():
    """
    Turns the volume up by 10%.

    tool_schema:
      properties: {}
    """
    try:
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        interface = cast(interface, POINTER(IAudioEndpointVolume))
        
        current_scalar = interface.GetMasterVolumeLevelScalar()
        new_scalar = min(1.0, current_scalar + 0.1)
        interface.SetMasterVolumeLevelScalar(new_scalar, None)
        
        msg = f"Volume turned up to {int(new_scalar * 100)}%."
        print(msg)
        return msg
    except Exception as e:
        error_msg = f"Error turning volume up: {e}"
        print(error_msg)
        return "Sorry, I couldn't change the volume."

def volume_down():
    """
    Turns the volume down by 10%.

    tool_schema:
      properties: {}
    """
    try:
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        interface = cast(interface, POINTER(IAudioEndpointVolume))
        current_scalar = interface.GetMasterVolumeLevelScalar()
        new_scalar = max(0.0, current_scalar - 0.1)
        interface.SetMasterVolumeLevelScalar(new_scalar, None)
        
        msg = f"Volume turned down to {int(new_scalar * 100)}%."
        print(msg)
        return msg
    except Exception as e:
        error_msg = f"Error turning volume down: {e}"
        print(error_msg)
        return "Sorry, I couldn't change the volume."

def set_brightness(level: int):
    """
    Sets the screen brightness to a specific level (0-100).

    tool_schema:
      properties:
        level:
          type: integer
          description: "The desired brightness level, from 0 to 100."
      required: ["level"]
    """
    if level is None:
        return "I'm sorry, I didn't catch the desired brightness level."
        
    try:
        level = int(level)
        if not (0 <= level <= 100):
            return "Please specify a brightness level between 0 and 100."
            
        sbc.set_brightness(level)
        msg = f"Brightness set to {level}%."
        print(msg)
        return msg
    except Exception as e:
        error_msg = f"Error setting brightness: {e}"
        print(error_msg)
        return "Sorry, I couldn't change the screen brightness."

def run_system_command(command: str):
    """
    Executes a shell command on the user's local Windows system via PowerShell and returns the stdout and stderr output.
    This gives the assistant full system access to run scripts, launch any executable, read system state, or manage files.

    tool_schema:
      properties:
        command:
          type: string
          description: "The complete PowerShell or command line command to run on the Windows machine."
      required: ["command"]
    """
    if not command:
        return "I didn't receive a command to execute."
    try:
        result = subprocess.run(
            ["powershell", "-Command", command],
            capture_output=True,
            timeout=15
        )
        import locale
        encoding = locale.getpreferredencoding(False) or 'utf-8'
        output = result.stdout.decode(encoding, errors='replace').strip()
        error = result.stderr.decode(encoding, errors='replace').strip()
        
        response = []
        if output:
            response.append(output)
        if error:
            response.append(f"Errors:\n{error}")
            
        if not response:
            return "Command executed successfully with no output."
        return "\n".join(response)
    except subprocess.TimeoutExpired:
        return "The command timed out after 15 seconds."
    except Exception as e:
        return f"Failed to execute command: {e}"

def configure_startup(enable: bool = True):
    """
    Configures Jarvis to start automatically and silently in the background when Windows boots up.

    tool_schema:
      properties:
        enable:
          type: boolean
          description: "Set to true to enable auto-start on Windows boot, or false to disable it."
      required: ["enable"]
    """
    import os
    startup_dir = os.path.join(os.environ["APPDATA"], "Microsoft\\Windows\\Start Menu\\Programs\\Startup")
    vbs_path = os.path.join(startup_dir, "JarvisSilent.vbs")
    
    if enable:
        workspace = "c:\\Users\\VINEET\\Desktop\\jarvis"
        pyw_path = os.path.join(workspace, "venv\\Scripts\\pythonw.exe")
        jarvis_py = os.path.join(workspace, "jarvis.py")
        
        # Windows Script Host launcher to run pythonw.exe completely invisibly (no console window)
        vbs_content = f'Set WshShell = CreateObject("WScript.Shell")\nWshShell.Run "cmd.exe /c \\""{pyw_path}\\" \\"{jarvis_py}\\"\\"", 0, False\n'
        try:
            with open(vbs_path, "w") as f:
                f.write(vbs_content)
            return "Auto-start enabled. I will start up silently in the background when Windows boots."
        except Exception as e:
            return f"Failed to enable auto-start: {e}"
    else:
        try:
            if os.path.exists(vbs_path):
                os.remove(vbs_path)
                return "Auto-start disabled."
            return "Auto-start was not enabled."
        except Exception as e:
            return f"Failed to disable auto-start: {e}"

# endregion
# -----------------------------------------------------------------------------
# region Tool Management System
# The following code automatically discovers, registers, and executes tools.
# -----------------------------------------------------------------------------

# --- Single Source of Truth for all available tool functions ---
TOOL_FUNCTIONS = [
    create_folder,
    open_url,           # replaces open_youtube + open_google + search_google
    open_application,
    create_file,
    get_time,
    tell_joke,
    get_weather,
    set_volume,
    volume_up,
    volume_down,
    set_brightness,
    run_system_command,
    configure_startup,
]

def _get_tool_schema(func):
    """Parses the 'tool_schema' from a function's docstring."""
    docstring = inspect.getdoc(func)
    if 'tool_schema:' not in docstring:
        return None
    
    # Extract the YAML part
    schema_yaml = docstring.split('tool_schema:')[1]
    try:
        return yaml.safe_load(schema_yaml)
    except yaml.YAMLError as e:
        print(f"Error parsing schema for {func.__name__}: {e}")
        return None

def _generate_llm_tools_json(tool_functions):
    """Generates the JSON definition for tools for the LLM."""
    llm_tools = []
    for func in tool_functions:
        schema = _get_tool_schema(func)
        if not schema:
            continue
            
        # The main description of the function for the LLM
        # is the part of the docstring *before* the 'tool_schema' block.
        description = inspect.getdoc(func).split('tool_schema:')[0].strip()
        
        tool_definition = {
            "type": "function",
            "function": {
                "name": func.__name__,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": schema.get("properties") or {},
                    "required": schema.get("required") or [],
                    "additionalProperties": False
                } 
            }
        }
        llm_tools.append(tool_definition)
    return llm_tools

def _generate_execution_map(tool_functions):
    """Creates a simple map from tool name to the actual Python function."""
    return {func.__name__: func for func in tool_functions}

# --- Generate tool configurations from the single source of truth ---
AVAILABLE_TOOLS_LLM_JSON = _generate_llm_tools_json(TOOL_FUNCTIONS)
AVAILABLE_TOOLS_EXECUTION_MAP = _generate_execution_map(TOOL_FUNCTIONS)

def execute_tool(tool_name, arguments):
    """
    Executes a single tool/function with given arguments using the generated map.
    """
    if tool_name in AVAILABLE_TOOLS_EXECUTION_MAP:
        function_to_call = AVAILABLE_TOOLS_EXECUTION_MAP[tool_name]
        try:
            # The AI provides arguments as a dictionary, which we can directly
            # use for keyword arguments in the Python function.
            result = function_to_call(**arguments)
            return result
        except TypeError as e:
            error_msg = f"Error calling tool '{tool_name}': Invalid arguments provided. {e}"
            print(error_msg)
            return error_msg
        except Exception as e:
            error_msg = f"An unexpected error occurred while executing tool '{tool_name}': {e}"
            print(error_msg)
            return error_msg
    else:
        return f"Error: Tool '{tool_name}' is not defined."

# endregion
# -----------------------------------------------------------------------------
# region AI Brain (LLM Interaction)
# -----------------------------------------------------------------------------

def ai_brain(query):
    """
    This is the main brain of the assistant.
    It uses a tool-enabled LLM to decide whether to answer directly or use a tool.
    """
   
    config = configparser.ConfigParser()
    config.read('config.ini')
    api_key = config.get('APIs', 'OpenRouter_api_key', fallback=None)
    llm_model = config.get('AI', 'model', fallback="arcee-ai/trinity-large-preview:free")
    api_endpoint = config.get('AI', 'api_endpoint', fallback="https://openrouter.ai/api/v1/chat/completions")

    if not api_key or "YOUR_API_KEY" in api_key:
        return "The AI Brain is not configured. Please add an OpenRouter API key to your config.ini file."

    system_prompt = """
    You are Jarvis, a witty and highly capable AI assistant. 
    Your personality is inspired by Jarvis from Iron Man.
    Always remember your user name as Vineet.
    Greet the user by name at the start of each interaction.
    You are helpful, a bit sarcastic, and always efficient.
    You can understand and respond in multiple languages, such as English and Hinglish.
    When a user asks a question, you must decide whether to use one of your available tools or answer directly.
    If you use a tool, simply call it. Do not add conversational text before calling a tool.
    If you don't need a tool, answer the user's question in a conversational and helpful manner.
    """
    conversation_history.append({"role": "user", "content": query})

    # 🔹 Keep only last 5 user+assistant pairs (10 messages)
    if len(conversation_history) > MAX_HISTORY_PAIRS * 2:
        conversation_history[:] = conversation_history[-MAX_HISTORY_PAIRS * 2:]

    messages = [{"role": "system", "content": system_prompt}] + conversation_history
    try:
        response = requests.post(
            url=api_endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": config.get('AI', 'site_url', fallback=''),
                "X-Title": config.get('AI', 'app_name', fallback='Jarvis Assistant')
            },
            json={
                "model": llm_model,
                "messages": messages,
                "tools": AVAILABLE_TOOLS_LLM_JSON, # Use the dynamically generated tools
                "tool_choice": "auto"
            },
            verify=certifi.where()
        )
        response.raise_for_status()
        data = response.json()
        message = data['choices'][0]['message']

        if message.get("tool_calls"):
            tool_call = message["tool_calls"][0]
            tool_name = tool_call['function']['name']
            
            import json
            try:
                arguments = json.loads(tool_call['function']['arguments'])
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON arguments from LLM: {e}")
                return "I received malformed instructions from my AI brain. Please try again."
            
            print(f"AI decided to use tool: {tool_name} with arguments: {arguments}")
            result = execute_tool(tool_name, arguments)

              # ✅ Store tool result as assistant reply for memory
            conversation_history.append({
                "role": "assistant",
                "content": result
            })

            return result
        else:
            assistant_reply = message['content']
            conversation_history.append({
            "role": "assistant",
            "content": assistant_reply
            })

            return assistant_reply

    except requests.exceptions.RequestException as e:
        print(f"Network error with AI Brain (OpenRouter): {e}")
        return "I'm having trouble connecting to my cognitive cloud. Please check the network connection."
    except Exception as e:
        print(f"Error with AI Brain (OpenRouter): {e}")
        return "I seem to be having a bit of trouble with my cognitive functions. Please check the API configuration."

# endregion