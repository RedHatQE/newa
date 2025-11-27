"""AI service for generating ReportPortal launch summaries."""

import json
from typing import Any, Optional

import requests

try:
    from attrs import define
except ModuleNotFoundError:
    from attr import define


# System prompt for AI model
# fmt: off
# ruff: noqa: E501
SYSTEM_PROMPT = """
You are a software tester providing summary report from test execution stored in individual ReportPortal launches. For each RP launch you start with a header containing launch name, URL, atrributes, description and test statistics. Then you state whether the review is complete or not, depending on the value of To investigate test failures. If the review is complete and all tests passed, mention it. If the review is not complete, add an action item to finish the review and state how many test failures needs to be reviewed. Then you prepare a summary of individual test failure categories. You do that by inspecting comments from testers made to individual test failures. Each failure is assigned to a category: Product bug, Automation bug, System issue or Not a defect. Individual failure are in the form: test name, Jira issue keys, reviewer's comment.

When presenting the summary of test failure categories, use descriptive category names such as "Product bugs related test failures", "Automation bugs related test failures", "System issues related test failures", and "Not a defect related test failures" instead of just the category names. Do not include a heading like "Summary of test failure categories" - present the categories directly. Skip any categories that have no test failures.

For each category, group test failures by their associated bug/issue. When multiple tests fail due to the same bug, mention the bug once along with the number of failing tests (e.g., "RHEL-12345 (3 failing tests): description of the bug"). Do not list individual test names when they share the same bug. Product bugs are the most important ones and should be tracked in the Jira bug tracking system. Automation bugs or System issues should ideally link either a Jira issue or a merge-request URL while the Jira issue may be using other project than RHEL.

When mentioning Jira issues in your summary, include the issue status and the fix version (from the "Fix Version/s" field) if available. Use "will be fixed in" wording when the status is not Closed or Done but Fix Version/s is set, and use "fixed in" for Closed or Done issues. For example: "RHEL-12345 [Status: In Progress, will be fixed in RHEL-10.0.0] (3 failing tests): description of the bug" or "RHEL-67890 [Status: Closed, fixed in RHEL-9.6.0]: description". If no fix version is set, omit that part (e.g., "RHEL-11111 [Status: New]").

When a test failure is not associated with any bug (marked as "???"), state that the test is missing a product bug and add a corresponding action item to track it. If some test failures are missing Jira issues or those Jira issues are reported for a different RHEL major release, do mention it in your summary as an action item. You can learn Jira issues details in the listing at the end of the report but do not include this listing in your summary.

If there are any action items, they should be listed at the end of the report under "Action items" heading. If there are no action items, do not include the "Action items" section at all.

For the summary report, use simple formatting so that the output can be directly copied to a Jira comment.
""".strip()
# fmt: on


@define
class AIService:
    """Service for interacting with AI models for generating summaries."""

    api_url: str
    api_token: str
    model: str

    def query_ai_model(self, user_message: str, system_prompt: Optional[str] = None) -> str:
        """Query the AI model with the given prompts.

        Args:
            user_message: The user message/input data
            system_prompt: The system prompt for the AI (defaults to SYSTEM_PROMPT)

        Returns:
            The AI model's response text
        """
        if system_prompt is None:
            system_prompt = SYSTEM_PROMPT

        if not self.api_url:
            raise Exception("AI API URL is not configured")

        if not self.api_token:
            raise Exception("AI API token is not configured")

        # Detect API type based on URL
        is_gemini = 'generativelanguage.googleapis.com' in self.api_url

        payload: dict[str, Any]
        if is_gemini:
            # Google Gemini API format
            # API key is passed as URL parameter
            # Support both full URL (with model embedded) and base URL + model
            if '/models/' in self.api_url and ':generateContent' in self.api_url:
                # Full URL provided - use as-is (backward compatible)
                url_with_key = f"{self.api_url}?key={self.api_token}"
            else:
                # Base URL provided - construct full URL with model
                base_url = self.api_url.rstrip('/')
                url_with_key = f"{base_url}/models/{self.model}:generateContent?key={self.api_token}"
            headers = {
                "Content-Type": "application/json",
                }

            # Combine system prompt and user message for Gemini
            combined_message = f"{system_prompt}\n\nThe report is below.\n\n{user_message}"

            payload = {
                "contents": [{
                    "parts": [{
                        "text": combined_message,
                        }],
                    }],
                }
        else:
            # OpenAI-compatible API format
            url_with_key = self.api_url
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_token}",
                }

            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                    ],
                }

        try:
            response = requests.post(url_with_key, headers=headers, json=payload, timeout=60)
            response.raise_for_status()

            result = response.json()

            # Extract response based on API type
            if is_gemini:
                return str(result['candidates'][0]['content']['parts'][0]['text'])
            return str(result['choices'][0]['message']['content'])
        except requests.exceptions.HTTPError as e:
            error_msg = f"Error querying AI model: {e}"
            try:
                error_detail = response.json()
                error_msg += f"\nResponse body: {json.dumps(error_detail, indent=2)}"
            except Exception:
                error_msg += f"\nResponse text: {response.text}"
            raise Exception(error_msg) from e
        except Exception as e:
            raise Exception(f"Error querying AI model: {e}") from e
