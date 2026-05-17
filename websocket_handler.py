class LiveDataHandler:
    def __init__(self, client):
        self.client = client
        self.connected = False
        self.socketio = None

    def set_socketio(self, socketio):
        self.socketio = socketio

    def connect(self):
        self.connected = True
        return True

    def disconnect(self):
        self.connected = False

    def subscribe(self, tokens, exchange_segment):
        return True

    def unsubscribe(self, tokens):
        return True


_handler = None


def init_live_data_handler(client):
    global _handler
    _handler = LiveDataHandler(client)
    return _handler


def get_live_data_handler():
    return _handler
