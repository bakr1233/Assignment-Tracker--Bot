"""What every source has to implement."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Tuple

from ..config import Config
from ..models import Assignment
from ..util import log


class Source:
    name = "base"
    label = "Base"
    #: True when this source cannot run without a visible browser the user
    #: can interact with (password prompt, Duo push).
    interactive = False
    #: Browser profile folder. Sites behind the same single sign-on share one,
    #: so approving Duo once covers all of them.
    profile = "default"

    def __init__(self, cfg: Config, visible: bool) -> None:
        self.cfg = cfg
        self.visible = visible
        self.log = log(self.name)

    # -- to implement ----------------------------------------------------

    def ready(self) -> Tuple[bool, str]:
        """(can we run, why not). Checked before fetch() so a half-filled
        credentials file is a skip with an explanation, not a crash."""
        raise NotImplementedError

    def fetch(self) -> List[Assignment]:
        raise NotImplementedError

    # -- helpers ---------------------------------------------------------

    @property
    def window(self) -> Tuple[datetime, datetime]:
        now = datetime.now(self.cfg.tz)
        return (
            now - timedelta(days=self.cfg.lookback_days),
            now + timedelta(days=self.cfg.lookahead_days),
        )

    def options(self) -> dict:
        return self.cfg.source(self.name)

    @property
    def mode(self) -> str:
        """"live" asks the site every run; "snapshot" replays a capture from
        disk, for sources not worth signing into hourly."""
        return str(self.options().get("mode") or "live").lower()

    @property
    def profile_name(self) -> str:
        return self.options().get("profile") or self.profile
