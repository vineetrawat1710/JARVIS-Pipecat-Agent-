from modules import actions

def understand(command_text):
    """
    This function now acts as a simple pass-through to the AI brain.
    The AI will handle understanding, routing, and entity extraction.

    Args:
        command_text (str): The text transcribed from the user's speech.

    Returns:
        str: The response from the AI or the result of a tool execution.
    """
    if not command_text:
        return "I'm sorry, I didn't hear anything."
    
    print(f"Sending to AI Brain: '{command_text}'")
    response = actions.ai_brain(command_text)
    return response