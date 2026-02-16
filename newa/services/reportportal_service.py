"""ReportPortal service integration."""

import time
import urllib.parse
from typing import TYPE_CHECKING, Optional

import requests

try:
    from attrs import define
except ModuleNotFoundError:
    from attr import define

if TYPE_CHECKING:
    import logging
    from typing import Any, TypeAlias

    JSON: TypeAlias = Any


HTTP_STATUS_CODES_OK = [200, 201]


@define
class ReportPortal:
    """ReportPortal integration service."""

    token: str
    url: str
    project: str

    def create_launch(self,
                      launch_name: str,
                      description: str,
                      attributes: Optional[dict[str, str]] = None) -> Optional[str]:
        query_data: JSON = {
            "attributes": [],
            "name": launch_name,
            'description': description,
            'startTime': str(int(time.time() * 1000)),
            }
        if attributes:
            for key, value in attributes.items():
                query_data['attributes'].append({"key": key.strip(), "value": value.strip()})
        data = self.post_request('/launch', json=query_data)
        if data:
            return str(data['id'])
        return None

    def finish_launch(self, launch_uuid: str, description: Optional[str] = None) -> Optional[str]:
        query_data: JSON = {
            'endTime': str(int(time.time() * 1000)),
            "status": "PASSED",
            }
        if description:
            query_data['description'] = description
        data = self.put_request(f'/launch/{launch_uuid}/finish', json=query_data)
        if data:
            return launch_uuid
        return None

    def update_launch(self,
                      launch_uuid: str,
                      description: Optional[str] = None,
                      attributes: Optional[dict[str, str]] = None,
                      extend: bool = False) -> Optional[str]:
        # RP API for update requires launch ID, not UUID
        info = self.get_launch_info(launch_uuid)
        if not info:
            raise Exception(
                f"Could not find launch {launch_uuid} in ReportPortal project {self.project}")
        launch_id = info['id']
        query_data: JSON = {
            "mode": "DEFAULT",
            }
        if description:
            if extend:
                query_data['description'] = f"{info['description']}<br>{description}"
            else:
                query_data['description'] = description
        if attributes:
            if extend:
                query_data['attributes'] = info['attributes']
            else:
                query_data['attributes'] = []
            for key, value in attributes.items():
                query_data['attributes'].append({"key": key.strip(), "value": value.strip()})
        data = self.put_request(f'/launch/{launch_id}/update', json=query_data, version=1)
        if data:
            return launch_uuid
        return None

    def get_launch_info(self, launch_uuid: str) -> 'JSON':
        return self.get_request(f'/launch/uuid/{launch_uuid}')

    def get_launch_url(self, launch_uuid: str) -> str:
        from urllib.parse import quote as Q  # noqa: N812
        return urllib.parse.urljoin(
            self.url, f"/ui/#{Q(self.project)}/launches/all/{Q(launch_uuid)}")

    def get_current_user_info(self) -> 'JSON':
        url = urllib.parse.urljoin(
            self.url, "/api/users")
        headers = {"Authorization": f"bearer {self.token}", "Content-Type": "application/json"}
        req = requests.get(url, headers=headers)
        if req.status_code in HTTP_STATUS_CODES_OK:
            return req.json()
        return None

    def check_connection(self, rp_url: str, logger: 'logging.Logger') -> None:
        try:
            rp_user_info = self.get_current_user_info()
            logger.debug(f"ReportPortal user is={rp_user_info['id']}")
            if not rp_user_info:
                raise Exception("Could not get ReportPortal user info.")
        except Exception as e:
            raise Exception(f"ReportPortal is not available at {rp_url}.") from e

    def check_for_empty_launch(self, launch_uuid: str,
                               logger: Optional['logging.Logger'] = None) -> bool:
        launch_info = self.get_launch_info(launch_uuid)
        empty = bool(not launch_info.get('statistics', {}).get('executions', {}))
        if logger and empty:
            logger.warning(f'WARN: Launch {launch_uuid} seems to be empty. '
                           '`tmt` reportportal plugin may not be enabled or configured properly.')
        return empty

    def get_request(self,
                    path: str,
                    params: Optional[dict[str, str]] = None,
                    version: int = 1) -> 'JSON':
        from urllib.parse import quote as Q  # noqa: N812
        url = urllib.parse.urljoin(
            self.url,
            f'/api/v{version}/{Q(self.project)}/{Q(path.lstrip("/"))}')
        if params:
            url = f'{url}?{urllib.parse.urlencode(params)}'
        headers = {"Authorization": f"bearer {self.token}", "Content-Type": "application/json"}
        req = requests.get(url, headers=headers)
        if req.status_code in HTTP_STATUS_CODES_OK:
            return req.json()
        return None

    def put_request(self,
                    path: str,
                    json: 'JSON',
                    version: int = 1) -> 'JSON':
        from urllib.parse import quote as Q  # noqa: N812
        url = urllib.parse.urljoin(
            self.url, f'/api/v{version}/{Q(self.project)}/{Q(path.lstrip("/"))}')
        headers = {"Authorization": f"bearer {self.token}", "Content-Type": "application/json"}
        req = requests.put(url, headers=headers, json=json)
        if req.status_code in HTTP_STATUS_CODES_OK:
            return req.json()
        return None

    def post_request(self,
                     path: str,
                     json: 'JSON',
                     version: int = 1) -> 'JSON':
        from urllib.parse import quote as Q  # noqa: N812
        url = urllib.parse.urljoin(
            self.url,
            f'/api/v{version}/{Q(self.project)}/{Q(path.lstrip("/"))}')
        headers = {"Authorization": f"bearer {self.token}", "Content-Type": "application/json"}
        req = requests.post(url, headers=headers, json=json)
        if req.status_code in HTTP_STATUS_CODES_OK:
            return req.json()
        return None
