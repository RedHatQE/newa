"""Helper functions for the Summarize command."""

import logging
import re
import textwrap
from typing import Any, Optional

from newa import ReportPortal

# Test item type mapping for ReportPortal
TEST_ITEM_TYPE_MAPPING = {
    "Product bugs": "pb001",
    "Automation bugs": "ab001",
    "System issues": "si001",
    "Not a defect": "nd001",
    "To Investigate": "ti001",
    }


def extract_jira_issues_from_comment(comment: str) -> list[str]:
    """Extract Jira issue IDs from comment text.

    Parses URLs like https://issues.redhat.com/browse/RHEL-12345
    Returns a list of issue IDs (e.g., ['RHEL-12345'])
    """
    if not comment:
        return []

    pattern = r'\b([A-Z]{2,20}-\d{4,8})\b'
    return re.findall(pattern, comment)


def get_launch_test_items_data(
        rp: ReportPortal,
        launch_id: int,
        item_type: str,
        logger: Optional[logging.Logger] = None) -> dict[str, Any]:
    """Extract test items data from ReportPortal launch with pagination support.

    Returns a dictionary with:
    - 'count': number of items
    - 'issues': dict mapping issue_id -> comment (for backward compatibility)
    - 'failures': list of individual test failures with their details
    """
    # Fetch all pages of test items
    all_content = []
    page_number = 1
    total_pages = 1

    while page_number <= total_pages:
        test_items = rp.get_request(
            '/item',
            {
                'filter.eq.launchId': str(launch_id),
                'page.size': '1024',
                'page.number': str(page_number),
                'filter.eq.issueType': TEST_ITEM_TYPE_MAPPING[item_type],
                })

        if not test_items or not test_items.get('content'):
            break

        all_content.extend(test_items['content'])

        # Update pagination info from response
        page_info = test_items.get('page', {})
        total_pages = page_info.get('totalPages', 1)
        total_elements = page_info.get('totalElements', len(all_content))

        # Log pagination info on first page
        if page_number == 1 and total_pages > 1 and logger:
            logger.info(
                f'{item_type}: Fetching {total_elements} items across {total_pages} pages')

        page_number += 1

    if not all_content:
        return {'count': 0, 'issues': {}, 'failures': []}

    count = len(all_content)

    if item_type == 'To Investigate':
        return {'count': count, 'issues': {}, 'failures': []}

    issues = {}
    failures = []

    for i in all_content:
        issue = i.get('issue', {})
        comment = issue.get('comment', '')

        # Collect issue IDs from external system issues
        issue_ids = [ext['ticketId'] for ext in issue.get('externalSystemIssues', [])]

        # Also parse Jira issues from comment text
        jira_issues_from_comment = extract_jira_issues_from_comment(comment)
        issue_ids.extend(jira_issues_from_comment)

        # Store individual failure
        failures.append({
            'name': i.get('name', 'Unknown'),
            'comment': comment,
            'issue_ids': issue_ids or ['???'],
            })

        # Store issues with their comments (for backward compatibility)
        if issue_ids:
            for issue_id in issue_ids:
                if issue_id not in issues:
                    issues[issue_id] = comment
        else:
            # No issue ID found
            issues['???'] = comment

    return {'count': count, 'issues': issues, 'failures': failures}


def format_launch_test_items(item_type: str, data: dict[str, Any]) -> list[str]:
    """Format test items data for a given item type.

    Args:
        item_type: Type of test item (e.g., 'Product bugs')
        data: Dictionary with 'count', 'issues', and 'failures' keys

    Returns:
        List of formatted lines
    """
    output: list[str] = []

    if data['count'] == 0:
        return output

    if item_type == 'To Investigate':
        output.append(f'{item_type}: {data["count"]}')
        return output

    output.append(f'{item_type}:')

    # Print individual failures
    for failure in data.get('failures', []):
        issue_keys = ', '.join(failure['issue_ids'])
        output.append(f'{4 * " "}{failure["name"]} [{issue_keys}]: {failure["comment"]}')

    return output


def format_jira_issue_details(jira_issues_data: dict[str, dict[str, Any]]) -> list[str]:
    """Format detailed information for Jira issues.

    Args:
        jira_issues_data: Dictionary mapping issue key to issue details.
                         For issues with errors, the dict will contain {'error': 'message'}

    Returns:
        List of formatted lines
    """
    if not jira_issues_data:
        return []

    output = [
        '=' * 80,
        'Jira Issue Details:',
        '=' * 80,
        '',
        ]

    for issue_key in sorted(jira_issues_data.keys()):
        issue = jira_issues_data[issue_key]

        # Check if this is an error entry
        if 'error' in issue:
            output.extend([
                f'Issue: {issue_key}',
                f'Error: {issue["error"]}',
                '',
                ])
        else:
            components = ", ".join(issue["components"]) if issue["components"] else "None"
            affects = ", ".join(issue["affects_versions"]) if issue["affects_versions"] else "None"
            fix = ", ".join(issue["fix_versions"]) if issue["fix_versions"] else "None"
            output.extend([
                f'Issue: {issue["key"]}',
                f'Summary: {issue["summary"]}',
                f'Status: {issue["status"]}',
                f'Component: {components}',
                f'Affects Version/s: {affects}',
                f'Fix Version/s: {fix}',
                '',
                ])

    return output


def format_statistics(launch_details: dict[str, Any], all_data: dict[str, dict[str, Any]]) -> str:
    """Format one-line statistics summary for the launch.

    Args:
        launch_details: Launch details from ReportPortal API
        all_data: Dictionary containing test items data for all issue types

    Returns:
        Formatted statistics string
    """
    stats = launch_details.get('statistics', {}).get('executions', {})

    total = stats.get('total', 0)
    passed = stats.get('passed', 0)
    product_bugs_count = all_data.get('Product bugs', {}).get('count', 0)
    automation_bugs_count = all_data.get('Automation bugs', {}).get('count', 0)
    system_issues_count = all_data.get('System issues', {}).get('count', 0)
    to_investigate = all_data.get('To Investigate', {}).get('count', 0)

    return (f'Test statistics: Total: {total}, Passed: {passed}, '
            f'Product bugs: {product_bugs_count}, Automation bugs: {automation_bugs_count}, '
            f'System issues: {system_issues_count}, To investigate: {to_investigate}')


def collect_launch_details(
        rp: ReportPortal,
        rp_url: str,
        rp_project: str,
        launch_id: int,
        logger: Optional[logging.Logger] = None) -> tuple[list[str], set[str]]:
    """Collect details for a ReportPortal launch.

    First reads all data, then formats it.

    Returns:
        Tuple of (formatted output lines, set of Jira issue keys)
    """
    output = []

    # Read launch details
    launch_details = rp.get_request(f'/launch/{launch_id}')

    if not launch_details:
        output.append(f'Error: Could not retrieve launch details for launch ID {launch_id}')
        return output, set()

    # Read test items data for all item types
    all_data = {}
    all_jira_issues = set()
    for item_type in TEST_ITEM_TYPE_MAPPING:
        all_data[item_type] = get_launch_test_items_data(rp, launch_id, item_type, logger)
        # Collect Jira issue keys (excluding '???')
        for issue_key in all_data[item_type]['issues']:
            if issue_key != '???':
                all_jira_issues.add(issue_key)

    # Print launch header
    attrs = ", ".join([f'{a["key"]}={a["value"]}' for a in launch_details["attributes"]])
    description = launch_details.get('description', '') or ''
    description = description.replace('<br>', '\n')

    output.extend([
        f'Launch name: {launch_details["name"]}',
        f'URL: {rp_url}/ui/#{rp_project}/launches/all/{launch_id}',
        f'Attributes:  {attrs}',
        f'Description: {textwrap.indent(description, 2 * " ")}',
        format_statistics(launch_details, all_data),
        '',
        ])

    # Print test items for all item types
    for item_type in TEST_ITEM_TYPE_MAPPING:
        output.extend(format_launch_test_items(item_type, all_data[item_type]))
        output.append('')

    return output, all_jira_issues
