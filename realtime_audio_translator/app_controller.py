import threading
from dataclasses import dataclass
from typing import Callable, Generic, Literal, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class TaskResult(Generic[T]):
    value: T | None = None
    error: Exception | None = None


@dataclass(frozen=True)
class TaskState:
    name: str
    status: Literal["started", "completed", "failed", "cancelled"]


class AppController:
    """Runs application commands away from Tk's event thread."""

    def __init__(self, post: Callable[..., None], state_changed: Callable[[TaskState], None] | None = None):
        self._post = post
        self._state_changed = state_changed
        self._tasks: dict[str, object] = {}
        self._cancelled: set[object] = set()
        self._tasks_lock = threading.Lock()
        self._engine_starting = False
        self._engine_stopping = False
        self.engine = None

    def submit(self, work: Callable[[], T], done: Callable[[TaskResult[T]], None], *, name: str = "") -> bool:
        token = object()
        if name:
            with self._tasks_lock:
                if name in self._tasks:
                    return False
                self._tasks[name] = token
            self._notify(TaskState(name, "started"))

        def run() -> None:
            try:
                result = TaskResult(value=work())
            except Exception as exc:
                result = TaskResult[T](error=exc)
            if name:
                with self._tasks_lock:
                    if self._tasks.get(name) is not token:
                        return
                    del self._tasks[name]
                    if token in self._cancelled:
                        self._cancelled.remove(token)
                        return
                self._notify(TaskState(name, "failed" if result.error else "completed"))
            self._post("callback", done, result)

        threading.Thread(target=run, daemon=True).start()
        return True

    def cancel(self, name: str | None = None) -> int:
        with self._tasks_lock:
            if name is None:
                names = [task_name for task_name, token in self._tasks.items() if token not in self._cancelled]
            else:
                token = self._tasks.get(name)
                names = [name] if token is not None and token not in self._cancelled else []
            for task_name in names:
                self._cancelled.add(self._tasks[task_name])
        for task_name in names:
            self._notify(TaskState(task_name, "cancelled"))
        return len(names)

    @property
    def busy(self) -> bool:
        with self._tasks_lock:
            return self._engine_starting or self._engine_stopping or any(token not in self._cancelled for token in self._tasks.values())

    def _notify(self, state: TaskState) -> None:
        if self._state_changed is not None:
            self._post("callback", self._state_changed, state)

    def start_engine(self, engine, done: Callable[[TaskResult[None]], None] | None = None) -> bool:
        with self._tasks_lock:
            if self.engine is not None or self._engine_starting or self._engine_stopping:
                return False
            self.engine = engine
            self._engine_starting = True
        self._notify(TaskState("engine_start", "started"))

        def finish(result: TaskResult[None]) -> None:
            with self._tasks_lock:
                self._engine_starting = False
                if result.error is not None or not engine.running:
                    if self.engine is engine:
                        self.engine = None
            self._notify(TaskState("engine_start", "failed" if result.error else "completed"))
            if done is not None:
                done(result)

        self.submit(engine.start, finish)
        return True

    def stop_engine(self, done: Callable[[TaskResult[str]], None]) -> bool:
        with self._tasks_lock:
            engine = self.engine
            if self._engine_stopping:
                return False
            if engine is None:
                self._post("callback", done, TaskResult(value=""))
                return True
            self._engine_stopping = True
        self._notify(TaskState("engine_stop", "started"))

        def stop() -> str:
            try:
                return engine.stop()
            finally:
                with self._tasks_lock:
                    if self.engine is engine:
                        self.engine = None

        def finish(result: TaskResult[str]) -> None:
            with self._tasks_lock:
                self._engine_stopping = False
            self._notify(TaskState("engine_stop", "failed" if result.error else "completed"))
            done(result)

        self.submit(stop, finish)
        return True
