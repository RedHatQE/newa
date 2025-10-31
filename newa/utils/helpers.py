"""Helper utility functions."""

import os
import time
import urllib

# common sleep times to avoid too frequent Jira API requests
SHORT_SLEEP = 1


def short_sleep() -> None:
    time.sleep(SHORT_SLEEP)


def get_url_basename(url: str) -> str:
    return os.path.basename(urllib.parse.urlparse(url).path)
