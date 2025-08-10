class LLM:
    def __init__(self, name, behavior_context):
        self.name = name
        self.behavior_context = behavior_context

    def send_message(self, message):
        # Simulate sending a message to the LLM and getting a response
        response = f"{self.name} received: {message}"
        return response

    def update_context(self, new_context):
        self.behavior_context = new_context

    def get_context(self):
        return self.behavior_context