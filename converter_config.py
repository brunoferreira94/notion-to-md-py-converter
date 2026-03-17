from dataclasses import dataclass
from typing import Optional


@dataclass
class ConverterConfig:
    page_url: str
    output: Optional[str] = None
    use_requests: bool = False
    screenshot: Optional[str] = None
    headful: bool = False
    ua: Optional[str] = None
    expand_toggles: bool = False
    max_scroll_steps: int = 220
    scroll_wait_ms: int = 250
    extract_selectables: bool = False
    no_extract_selectables: bool = False
    download_assets: bool = False
    assets_dir: Optional[str] = None
    follow_subpages: bool = False
