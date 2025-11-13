"""Helper utility functions."""

import os
import re
import time
import urllib

# common sleep times to avoid too frequent Jira API requests
SHORT_SLEEP = 1


def short_sleep() -> None:
    time.sleep(SHORT_SLEEP)


def get_url_basename(url: str) -> str:
    return os.path.basename(urllib.parse.urlparse(url).path)


def els_release_check(release: str) -> bool:
    """Returns True if the release is ELS release"""
    return bool(re.search(r'(RHEL-7-ELS|\.Z\..*(AUS|TUS|E.S))', release))
