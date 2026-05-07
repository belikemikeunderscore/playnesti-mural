from dataclasses import dataclass, field


@dataclass
class AppState:
    ws_clients: set = field(default_factory=set)
    media_items: list = field(default_factory=list)
