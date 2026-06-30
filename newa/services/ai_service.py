"""AI service for generating ReportPortal launch summaries."""

import json
import os
from pathlib import Path
from typing import Any, Optional

import requests

try:
    from attrs import define, field
except ModuleNotFoundError:
    from attr import define, field

# Optional OAuth2 dependencies
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    HAS_GOOGLE_AUTH = True
except ImportError:
    HAS_GOOGLE_AUTH = False


# System prompt for AI model
# fmt: off
# ruff: noqa: E501
SYSTEM_PROMPT = """
ROLE: You are a critical and rigorous Senior Software Quality Analyst. Your goal is to synthesize test execution data from ReportPortal launches into a concise, professional summary report suitable for posting as a Jira comment.

INPUT DATA:

    RP Launch Data: Includes Name, URL, Attributes, Description (mixed with testing job request stats like REQ-X.Y.Z), Test Statistics, and individual failures with comments/Jira IDs.

    Jira Issue Data: Details for all Jira issues mentioned in the test comments (Status, Fix Version, etc.).

REPORT GENERATION RULES:

1. HEADER SECTION Generate a header with the following fields:

    Name: <Launch Name>

    Description: <Launch Description> (Important: Remove any text regarding "REQ-X.Y.Z" testing job requests and statuses from this field).

    URL: <Launch URL>

    Attributes: <Comma separated list of attributes>

    Test statistics: <Provided stats>

2. REVIEW STATUS SECTION Analyze the completeness of the test run and review.

    Label: "Review status:"

    Logic:

        State clearly if all failed tests have been reviewed.

        Check the "REQ-X.Y.Z" request statuses in the original description. IMPORTANT: Request statuses 'passed' and 'failed' both indicate COMPLETE test execution - do NOT mark these as incomplete. Only mark testing job requests as incomplete if there are request statuses OTHER than 'passed' or 'failed' (e.g., 'pending', 'running', 'error', etc.).

        If any tests are 'skipped' or 'error', explicitly state that results are possibly incomplete and why.

        Example: "All failures reviewed. However, execution is incomplete (3 skipped tests)."

3. REVIEW SUMMARY SECTION Group specific test failures into the following categories:

    Label: "Review summary:"

    Product bug related test failures

    Automation bug related test failures

    System issue related test failures

    Not a defect test failures

    Grouping Rule: Within each category, group failures by their associated Jira Issue ID. If multiple tests fail due to the same Jira, list the Jira once and provide the count of failing tests. For failures without a Jira issue (no issue ID in the input), list them directly without any ID prefix — just the count and comment.

    Jira Formatting Rule:

        Format: JIRA-ID [Status: <Status>, <Version Info>]: <Count> failing tests - <Consolidated Comment>

        For failures without a Jira issue: <Count> failing tests - <Consolidated Comment> (no JIRA-ID prefix, no brackets)

            Never refer Jira issues using full URL (starts with https://issues.redhat.com/) but only the issue ID/key.

        Version Info Logic:

            IF Status is "Closed" OR "Done" → Use: fixed in <Fix Version>

            IF Status is NOT "Closed/Done" AND "Fix Version" exists → Use: will be fixed in <Fix Version>

            IF No Fix Version exists → Omit the version info part.

    Exclusion: If a category has no failures, do not list the category header. If there are 0 failures in the entire launch, omit this whole section.

4. FEEDBACK SECTION Analyze the data for discrepancies or action items.

    Label: "Feedback:"

    Triggers (Include this section only if these exist):

        Missing Jira: Any failure categorized as a bug but missing a Jira ID (indicated by "{jira_none_id}").

        Missing Link: Failures missing both a Jira ID and a Pull Request URL.

        Project Mismatch: Issues reported for a different product major release (e.g., RHEL-9 vs RHEL-10 version mismatch). Minor version mismatch (e.g. RHEL-10.1 vs RHEL-10.2) is acceptable for not-closed bugs and should be ignored. Ignore upper/lower case differences.

        Version Logic Discrepancy: A failing test linked to a Jira that is already marked as "Fix Version/s:" in an earlier version than the current test candidate.

    Exclusion: If no feedback is generated, omit this section.

FORMATTING:

    Use simple text formatting compatible with Jira comments.

    Do not use Markdown (like **bold** or [link](url)). Use Jira style referencing if necessary, or plain text.

    Ensure the tone is objective and analytical.
""".strip()
# fmt: on


@define
class AIService:
    """Service for interacting with AI models for generating summaries."""

    api_url: str
    api_token: str
    model: str
    oauth2_client_secret_file: str = ''
    oauth2_scopes: str = ''
    oauth2_token_file: str = ''

    # Cached OAuth2 credentials
    _credentials: Optional[Any] = field(default=None, init=False, repr=False)

    def _get_oauth2_credentials(self) -> Any:
        """Get or refresh OAuth2 credentials for Google APIs.

        Returns:
            Google OAuth2 Credentials object

        Raises:
            Exception: If OAuth2 libraries are not available or authentication fails
        """
        if not HAS_GOOGLE_AUTH:
            raise Exception(
                "OAuth2 authentication requires google-auth-oauthlib and google-auth packages. "
                "Install them with: pip install newa[oauth2]")

        if self._credentials and self._credentials.valid:
            return self._credentials

        creds = None
        # Expand ~ and environment variables in paths
        if self.oauth2_token_file:
            token_file = Path(os.path.expandvars(self.oauth2_token_file)).expanduser()
        else:
            token_file = Path.home() / '.newa_oauth2_token.json'
        client_secret_file = Path(os.path.expandvars(
            self.oauth2_client_secret_file)).expanduser()

        # Load cached token if it exists
        if token_file.exists():
            try:
                # Determine scopes for credentials validation
                scopes = None
                if self.oauth2_scopes:
                    scopes = [s.strip() for s in self.oauth2_scopes.split(',') if s.strip()]
                creds = Credentials.from_authorized_user_file(str(token_file), scopes)
            except Exception:
                # Token file is corrupted or invalid, will re-authenticate
                pass

        # Refresh or get new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                # Refresh expired token
                try:
                    creds.refresh(Request())
                except Exception:
                    # Refresh failed, need to re-authenticate
                    creds = None

            if not creds:
                # Perform OAuth2 flow
                if not client_secret_file.exists():
                    raise Exception(
                        f"OAuth2 client secret file not found: {client_secret_file}. "
                        "Please download it from Google Cloud Console and save it to this path.")

                # Default scopes for Gemini API
                if self.oauth2_scopes:
                    scopes = [s.strip() for s in self.oauth2_scopes.split(',') if s.strip()]
                else:
                    scopes = ['https://www.googleapis.com/auth/generative-language.retriever']

                flow = InstalledAppFlow.from_client_secrets_file(
                    str(client_secret_file), scopes)

                # Try local server flow first (for desktop/interactive environments)
                # Fall back to containerized flow if it fails (for containers/headless
                # environments)
                try:
                    creds = flow.run_local_server(port=0)
                except Exception:
                    # Local server failed (likely in container or headless environment)
                    # Use local server without opening browser instead
                    # Note: For containerized environments, port 6392 must be exposed
                    # (6392 is 'NEWA' on keypad)
                    print("\nBrowser-based authentication not available (container/headless environment).")
                    print("Using local server flow with manual URL on port 6392...\n")
                    print("NOTE: For containerized environments, ensure port 6392 is exposed.\n")
                    creds = flow.run_local_server(port=6392, open_browser=False)

            # Save credentials for next run
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(creds.to_json())

        self._credentials = creds
        return creds

    def _is_oauth2_configured(self) -> bool:
        """Check if OAuth2 is configured (client secret file is specified)."""
        if not self.oauth2_client_secret_file:
            return False
        # Expand ~ and environment variables in the path
        expanded_path = Path(os.path.expandvars(
            self.oauth2_client_secret_file)).expanduser()
        return expanded_path.exists()

    def query_ai_model(self, user_message: str, system_prompt: Optional[str] = None) -> str:
        """Query the AI model with the given prompts.

        Supports both API key and OAuth2 authentication.
        OAuth2 is used if oauth2_client_secret_file is configured.

        Args:
            user_message: The user message/input data
            system_prompt: The system prompt for the AI (defaults to SYSTEM_PROMPT)

        Returns:
            The AI model's response text
        """
        if system_prompt is None:
            from newa.cli.constants import JIRA_NONE_ID
            system_prompt = SYSTEM_PROMPT.format(jira_none_id=JIRA_NONE_ID)

        if not self.api_url:
            raise Exception("AI API URL is not configured")

        # Detect API type based on URL
        is_gemini = 'generativelanguage.googleapis.com' in self.api_url

        # Determine authentication method
        use_oauth2 = is_gemini and self._is_oauth2_configured()

        payload: dict[str, Any]
        if use_oauth2:
            # OAuth2 authentication for Gemini
            creds = self._get_oauth2_credentials()
            access_token = creds.token

            # Construct URL
            if '/models/' in self.api_url and ':generateContent' in self.api_url:
                url_with_key = self.api_url
            else:
                base_url = self.api_url.rstrip('/')
                url_with_key = f"{base_url}/models/{self.model}:generateContent"

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token}",
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

        elif is_gemini:
            # API key authentication for Gemini (existing code)
            if not self.api_token:
                raise Exception("AI API token is not configured")

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
            if not self.api_token:
                raise Exception("AI API token is not configured")

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
