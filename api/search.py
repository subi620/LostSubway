from lost112.config import Settings
from server import App, Handler


class handler(Handler):
    app = App(Settings.load())
