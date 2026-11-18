"""Vercel Functions가 기존 API Handler를 재사용하는 진입점."""
from server import App, Handler
from lost112.config import Settings


class handler(Handler):
    app = App(Settings.load())
