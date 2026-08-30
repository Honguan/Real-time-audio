import threading
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class TaskResult(Generic[T]):
    value: T | None = None
    error: Exception | None = None


class AppController:
    """Runs application commands away from Tk's event thread."""

    def __init__(self, post: Callable[..., None]):
        self._post = post
        self.engine = None

    def submit(self, work: Callable[[], T], done: Callable[[TaskResult[T]], None]) -> None:
        def run() -> None:
            try:
                result = TaskResult(value=work())
            except Exception as exc:
                result = TaskResult[T](error=exc)
            self._post("callback", done, result)

        threading.Thread(target=run, daemon=True).start()

    def start_engine(self, engine) -> None:
        self.engine = engine
        self.submit(engine.start, lambda _result: None)

    def stop_engine(self, done: Callable[[TaskResult[str]], None]) -> None:
        engine = self.engine
        if engine is None:
            self._post("callback", done, TaskResult(value=""))
            return

        def stop() -> str:
            try:
                return engine.stop()
            finally:
                if self.engine is engine:
                    self.engine = None

        self.submit(stop, done)
