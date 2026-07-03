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
    from typing import Any

    from typing_extensions import TypeAlias

    JSON: TypeAlias = Any


HTTP_STATUS_CODES_OK = [200, 201]


class ReportPortalError(Exception):
    """Raised when a ReportPortal API request fails with a non-OK status code."""

    def __init__(self, status_code: int, url: str, response_text: str):
        self.status_code = status_code
        self.url = url
        self.response_text = response_text
        super().__init__(
            f"ReportPortal API request failed: {status_code} {url}\n"
            f"Response: {response_text[:500]}")


@define
class ReportPortal:
    """ReportPortal integration service."""

    token: str
    url: str
    project: str

    def create_launch(self,
                      launch_name: str,
                      description: str,
                      attributes: Optional[dict[str, str]] = None) -> str:
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
        return str(data['id'])

    def finish_launch(self, launch_uuid: str, description: Optional[str] = None) -> str:
        query_data: JSON = {
            'endTime': str(int(time.time() * 1000)),
            "status": "PASSED",
            }
        if description:
            query_data['description'] = description
        self.put_request(f'/launch/{launch_uuid}/finish', json=query_data)
        return launch_uuid

    def update_launch(self,
                      launch_uuid: str,
                      description: Optional[str] = None,
                      attributes: Optional[dict[str, str]] = None,
                      extend: bool = False) -> str:
        # RP API for update requires launch ID, not UUID
        info = self.get_launch_info(launch_uuid)
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
        self.put_request(f'/launch/{launch_id}/update', json=query_data, version=1)
        return launch_uuid

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
        raise ReportPortalError(req.status_code, url, req.text)

    def check_connection(self, rp_url: str, logger: 'logging.Logger') -> None:
        try:
            rp_user_info = self.get_current_user_info()
            logger.debug(f"ReportPortal user is={rp_user_info['id']}")
        except ReportPortalError as e:
            raise ReportPortalError(
                e.status_code, e.url,
                f"ReportPortal is not available at {rp_url}") from e

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
        raise ReportPortalError(req.status_code, url, req.text)

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
        raise ReportPortalError(req.status_code, url, req.text)

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
        raise ReportPortalError(req.status_code, url, req.text)

    def delete_request(self,
                       path: str,
                       version: int = 1) -> bool:
        from urllib.parse import quote as Q  # noqa: N812
        url = urllib.parse.urljoin(
            self.url,
            f'/api/v{version}/{Q(self.project)}/{Q(path.lstrip("/"))}')
        headers = {"Authorization": f"bearer {self.token}", "Content-Type": "application/json"}
        req = requests.delete(url, headers=headers)
        return req.status_code in HTTP_STATUS_CODES_OK

    def remove_test_suite_by_tag(self,
                                 launch_uuid: str,
                                 newa_batch_id: str,
                                 logger: Optional['logging.Logger'] = None) -> bool:
        """
        Remove a test suite from a ReportPortal launch by newa_batch tag.

        Args:
            launch_uuid: UUID of the ReportPortal launch
            newa_batch_id: NEWA batch ID for precise targeting (unique per request execution)
            logger: Optional logger for debug messages

        Returns:
            True if suite was found and removed, False otherwise
        """
        # First get launch info to retrieve numeric launch ID
        try:
            launch_info = self.get_launch_info(launch_uuid)
        except ReportPortalError:
            if logger:
                logger.warning(f'Could not find launch {launch_uuid} in ReportPortal')
            return False

        launch_id = launch_info['id']

        # Search for test items (suites) with the newa_batch tag
        # Using the search API with filter for attributes
        # The correct syntax for filtering by attribute key:value is
        # filter.has.compositeAttribute=key:value
        params = {
            'filter.eq.launchId': str(launch_id),
            'filter.eq.type': 'suite',
            'filter.has.compositeAttribute': f'newa_batch:{newa_batch_id}',
            }

        # Get test items matching the criteria (with pagination support)
        # ReportPortal API returns paginated results, so we need to iterate through all pages
        page = 1
        page_size = 50  # ReportPortal default page size
        deleted_items = []
        failed_items = []

        while True:
            # Add pagination parameters
            paginated_params = params.copy()
            paginated_params['page.page'] = str(page)
            paginated_params['page.size'] = str(page_size)

            try:
                items = self.get_request('/item', params=paginated_params, version=1)
            except ReportPortalError:
                break

            if not items.get('content'):
                # No more items on this page
                break

            # Delete each matching suite on this page
            for item in items['content']:
                item_id = item['id']
                if logger:
                    logger.info(
                        f'Removing test suite {item_id} with tag newa_batch={newa_batch_id} '
                        f'from launch {launch_uuid}')
                success = self.delete_request(f'/item/{item_id}', version=1)
                if success:
                    deleted_items.append(item_id)
                else:
                    failed_items.append(item_id)
                    if logger:
                        logger.warning(
                            f'Failed to remove test suite {item_id} from launch {launch_uuid}')

            # Check if there are more pages
            page_metadata = items.get('page', {})
            total_pages = page_metadata.get('totalPages', 1)
            if page >= total_pages:
                break

            page += 1

        # Log summary
        if not deleted_items and not failed_items:
            if logger:
                logger.debug(
                    f'No test suite found with tag newa_batch={newa_batch_id} '
                    f'in launch {launch_uuid}')
            return False

        if failed_items and logger:
            logger.warning(
                f'Failed to remove {len(failed_items)} test suite(s) from launch {launch_uuid}: '
                f'{", ".join(str(i) for i in failed_items)}')

        # Return True if at least one item was successfully deleted
        return len(deleted_items) > 0
