"""Helper functions for the Jira command."""

import copy
import re
import urllib.parse
from collections.abc import Generator
from re import Pattern
from typing import Any, Optional

from newa import (
    ArtifactJob,
    CLIContext,
    ErrataTool,
    ErratumCommentTrigger,
    EventType,
    Issue,
    IssueAction,
    IssueConfig,
    IssueHandler,
    JiraJob,
    OnRespinAction,
    Recipe,
    RoGCommentTrigger,
    RoGTool,
    eval_test,
    render_template,
    short_sleep,
    )
from newa.cli.constants import JIRA_NONE_ID


def _parse_issue_mapping(map_issue: list[str], config: IssueConfig) -> dict[str, str]:
    """Parse and validate issue mapping from command line arguments."""
    issue_mapping: dict[str, str] = {}

    # Parse --map-issue keys and values into a dictionary
    for m in map_issue:
        r = re.fullmatch(r'([^\s=]+)=([^=]*)', m)
        if not r:
            raise Exception(f"Mapping {m} does not having expected format 'key=value'")
        key, value = r.groups()
        issue_mapping[key] = value

    # Gather ids from the config file
    ids = [getattr(action, "id", None) for action in config.issues[:]]

    # Check for keys not present in a config file
    for key in issue_mapping:
        if key not in ids:
            raise Exception(f"Key '{key}' from mapping '{m}' doesn't match issue item id "
                            f"from the config file. Typo?")

    return issue_mapping


def _create_jira_fake_id_generator() -> Generator[str, int, None]:
    """Generate fake Jira IDs for jobs without actual Jira issues."""
    n = 1
    while True:
        yield f'{JIRA_NONE_ID}_{n}'
        n += 1


def _get_jira_event_fields(
        ctx: CLIContext,
        artifact_job: ArtifactJob,
        jira_handler: IssueHandler) -> Any:
    """Get Jira event fields for Jinja template usage."""
    if artifact_job.event.type_ is EventType.JIRA:
        jira_event_fields = jira_handler.get_details(Issue(artifact_job.event.id)).fields
        jira_event_fields.id = artifact_job.event.id
        short_sleep()
    else:
        jira_event_fields = {}
    return jira_event_fields


def _render_action_value(
        value: str,
        artifact_job: ArtifactJob,
        action: IssueAction,
        jira_event_fields: dict[str, Any]) -> str:
    """Render a single value as Jinja template.

    Args:
        value: Template string to render
        artifact_job: Job context
        action: Action context
        jira_event_fields: Jira event fields

    Returns:
        Rendered value as string
    """
    return render_template(
        value,
        EVENT=artifact_job.event,
        ERRATUM=artifact_job.erratum,
        COMPOSE=artifact_job.compose,
        JIRA=jira_event_fields,
        ROG=artifact_job.rog,
        CONTEXT=action.context,
        ENVIRONMENT=action.environment)


def _render_action_fields(
        action: IssueAction,
        artifact_job: ArtifactJob,
        jira_event_fields: dict[str, Any],
        assignee: Optional[str],
        unassigned: bool) -> tuple[str, str, Optional[str], dict[str, Any],
                                   dict[str, list[str]], bool]:
    """Render all action fields using Jinja templates."""
    rendered_summary = _render_action_value(
        action.summary or '', artifact_job, action, jira_event_fields)
    rendered_description = _render_action_value(
        action.description or '', artifact_job, action, jira_event_fields)

    # Determine assignee
    if assignee:
        rendered_assignee = assignee
    elif action.assignee:
        rendered_assignee = _render_action_value(
            action.assignee or '', artifact_job, action, jira_event_fields)
    else:  # covers unassigned as well
        rendered_assignee = None

    # Render newa_id if present
    # NOTE: This mutation is intentional and necessary. IssueHandler.newa_id() uses
    # action.newa_id directly without rendering (see __init__.py:1831-1832), so the
    # rendered value must be stored back in the action object. While actions can be
    # re-queued (line 1200), each action is only processed once, and the mutation only
    # happens during that single processing pass. Actions come from a shallow copy of
    # config.issues[:] (line 1154).
    if action.newa_id:
        action.newa_id = _render_action_value(
            action.newa_id, artifact_job, action, jira_event_fields)

    # Render custom fields
    rendered_fields: dict[str, Any] = copy.deepcopy(action.fields) if action.fields else {}
    if rendered_fields:
        for key, value in rendered_fields.items():
            if isinstance(value, str):
                rendered_fields[key] = _render_action_value(
                    value, artifact_job, action, jira_event_fields)
            elif isinstance(value, list):
                rendered_fields[key] = [_render_action_value(
                    v, artifact_job, action, jira_event_fields) for v in value]

    # Render links
    rendered_links: dict[str, list[str]] = {}
    if action.links:
        from newa.utils.yaml_utils import yaml_parser

        for relation in action.links:
            rendered_links[relation] = []
            link_values = action.links[relation]

            # Handle case where links value is a template reference to a list
            # e.g., "{{ ERRATUM.jira_issues }}" which should evaluate to a list
            if isinstance(link_values, str):
                rendered = _render_action_value(
                    link_values, artifact_job, action, jira_event_fields)
                try:
                    # Parse rendered string as YAML to get native type (same as recipes.py)
                    parsed = yaml_parser().load(rendered)
                    if isinstance(parsed, list):
                        # Validate and normalize each element to a string
                        rendered_links[relation] = [str(v) for v in parsed]
                    else:
                        # Single value
                        rendered_links[relation] = [str(parsed)]
                except Exception:
                    # If YAML parsing fails, treat as single string value
                    rendered_links[relation] = [rendered]
            elif isinstance(link_values, list):
                # List of individual template strings
                for linked_key in link_values:
                    if isinstance(linked_key, str):
                        rendered_links[relation].append(_render_action_value(
                            linked_key, artifact_job, action, jira_event_fields))
                    else:
                        raise Exception(
                            f"Linked issue key '{linked_key}' must be a string")
            else:
                raise Exception(
                    f"Links value for '{relation}' must be a string or list")

    # Render schedule if it's a string
    rendered_schedule: bool
    if isinstance(action.schedule, str):
        rendered_schedule_str = _render_action_value(
            action.schedule, artifact_job, action, jira_event_fields)
        # Convert rendered string to boolean (strip whitespace before comparing)
        rendered_schedule = rendered_schedule_str.strip().lower() in ('true', '1', 'yes')
    elif action.schedule is None:
        # Treat None as True (default behavior)
        rendered_schedule = True
    else:
        rendered_schedule = action.schedule

    return (rendered_summary, rendered_description, rendered_assignee,
            rendered_fields, rendered_links, rendered_schedule)


def _find_or_create_issue(
        ctx: CLIContext,
        action: IssueAction,
        jira_handler: IssueHandler,
        config: IssueConfig,
        issue_mapping: dict[str, str],
        no_newa_id: bool,
        recreate: bool,
        rendered_summary: str,
        rendered_description: str,
        rendered_assignee: Optional[str],
        rendered_fields: dict[str, Any],
        rendered_links: dict[str, list[str]],
        processed_actions: dict[str, Issue],
        created_action_ids: list[str]) -> tuple[Optional[Issue], list[Issue], bool]:
    """
    Find existing issue or create a new one.

    Returns (issue, old_issues, trigger_comment).
    Returns (None, [], False) if action should be skipped.
    """
    new_issues: list[Issue] = []
    old_issues: list[Issue] = []
    create_new_issue = not ctx.issue_id_filter_pattern

    # Get transition settings
    transition_passed = None
    transition_processed = None
    if action.auto_transition:
        if jira_handler.transitions.passed:
            transition_passed = jira_handler.transitions.passed[0]
        if jira_handler.transitions.processed:
            transition_processed = jira_handler.transitions.processed[0]

    # First check if we have a match in issue_mapping
    if action.id and action.id in issue_mapping and issue_mapping[action.id].strip():
        mapped_issue = Issue(
            issue_mapping[action.id].strip(),
            group=config.group,
            transition_passed=transition_passed,
            transition_processed=transition_processed)
        jira_issue = jira_handler.get_details(mapped_issue)
        mapped_issue.closed = jira_issue.get_field(
            "status").name in jira_handler.transitions.closed
        new_issues.append(mapped_issue)

    # Otherwise search for the issue in Jira
    elif not no_newa_id:
        short_sleep()
        if recreate:
            search_result = jira_handler.get_related_issues(
                action, all_respins=True, closed=False)
        else:
            search_result = jira_handler.get_related_issues(
                action, all_respins=True, closed=True)

        for jira_issue_key, jira_issue in search_result.items():
            ctx.logger.info(f"Checking {jira_issue_key}")

            is_new = False
            if jira_handler.newa_id(action) in jira_issue["description"] \
                and (not action.parent_id
                     or action.parent_id not in created_action_ids):
                is_new = True

            if is_new:
                new_issues.append(
                    Issue(
                        jira_issue_key,
                        group=config.group,
                        closed=jira_issue["status"] == "closed",
                        transition_passed=transition_passed,
                        transition_processed=transition_processed))
            elif jira_issue["status"] == "opened":
                old_issues.append(
                    Issue(
                        jira_issue_key,
                        group=config.group,
                        closed=False,
                        transition_passed=transition_passed,
                        transition_processed=transition_processed))

    # Old opened issue(s) can be re-used for the current respin
    if old_issues and action.on_respin in (OnRespinAction.KEEP, OnRespinAction.UPDATE):
        new_issues.extend(old_issues)
        old_issues = []

    # Unless we want recreate closed issues we would stop processing
    if new_issues and (not recreate):
        opened_issues = [i for i in new_issues if not i.closed]
        closed_issues = [i for i in new_issues if i.closed]
        if not opened_issues:
            closed_ids = ', '.join([i.id for i in closed_issues])
            ctx.logger.info(
                f"Relevant issues {closed_ids} found but already closed")
            return None, [], False  # Signal to skip this action

        new_issues = opened_issues

    # Apply issue_id_filter if specified
    if ctx.issue_id_filter_pattern and new_issues:
        filtered_issues = [
            issue for issue in new_issues
            if not ctx._should_filter_by_issue_id(issue.id)]
        new_issues = filtered_issues

    # Create new issue or reuse existing
    trigger_comment = False
    if not new_issues:
        if create_new_issue:
            parent = None
            if action.parent_id:
                parent = processed_actions.get(action.parent_id)

            short_sleep()
            new_issue = jira_handler.create_issue(
                action=action,
                summary=rendered_summary,
                description=rendered_description,
                use_newa_id=not no_newa_id,
                assignee_email=rendered_assignee,
                parent=parent,
                group=config.group,
                transition_passed=transition_passed,
                transition_processed=transition_processed,
                fields=rendered_fields,
                links=rendered_links)

            # action.id is guaranteed to be non-None due to validation in _process_issue_config
            assert action.id is not None
            processed_actions[action.id] = new_issue
            created_action_ids.append(action.id)
            ctx.logger.info(f"New issue {new_issue.id} created")
            trigger_comment = True
        else:
            return None, [], False  # Signal to skip this action

    elif len(new_issues) == 1:
        new_issue = new_issues[0]
        assert action.id is not None
        processed_actions[action.id] = new_issue
        short_sleep()

        # Handle UPDATE vs KEEP behavior
        if action.on_respin == OnRespinAction.UPDATE:
            # Update issue summary, description, custom fields (and later add missing links)
            if not no_newa_id:
                ctx.logger.debug(f"Calling update_issue for {new_issue.id}")
                trigger_comment = jira_handler.update_issue(
                    action, new_issue, rendered_summary, rendered_description,
                    fields=rendered_fields)
                ctx.logger.debug(f"update_issue returned: {trigger_comment}")
            else:
                ctx.logger.info("Skipping issue update due to --no-newa-id flag")
            ctx.logger.info(f"Issue {new_issue} updated for respin")
        else:
            # KEEP behavior - just refresh the NEWA ID
            if not no_newa_id:
                ctx.logger.debug(f"Calling refresh_issue for {new_issue.id}")
                trigger_comment = jira_handler.refresh_issue(action, new_issue)
                ctx.logger.debug(f"refresh_issue returned: {trigger_comment}")
            else:
                ctx.logger.info("Skipping issue refresh due to --no-newa-id flag")
            ctx.logger.info(f"Issue {new_issue} re-used")

        # Add issue links from action configuration
        jira_handler.add_issue_links(new_issue, rendered_links)

    else:
        raise Exception(f"More than one new {action.id} found ({new_issues})!")

    return new_issue, old_issues, trigger_comment


def _handle_erratum_comment_for_jira(
        ctx: CLIContext,
        et: ErrataTool,
        artifact_job: ArtifactJob,
        action: IssueAction,
        new_issue: Issue,
        rendered_summary: str,
        trigger_comment: bool) -> None:
    """Add comment to Errata Tool if required."""
    if (ctx.settings.et_enable_comments and
            trigger_comment and
            action.erratum_comment_triggers and
            ErratumCommentTrigger.JIRA in action.erratum_comment_triggers and
            artifact_job.erratum):
        issue_url = urllib.parse.urljoin(
            ctx.settings.jira_url, f"/browse/{new_issue.id}")
        et.add_comment(
            artifact_job.erratum.id,
            'New Errata Workflow Automation (NEWA) prepared '
            'a Jira tracker for this advisory.\n'
            f'{new_issue.id} - {rendered_summary}\n'
            f'{issue_url}')
        ctx.logger.info(
            f"Erratum {artifact_job.erratum.id} was updated "
            f"with a comment about {new_issue.id}")


def _handle_rog_comment_for_jira(
        ctx: CLIContext,
        rog: RoGTool,
        artifact_job: ArtifactJob,
        action: IssueAction,
        new_issue: Issue,
        rendered_summary: str,
        trigger_comment: bool) -> None:
    """Add private comment to RoG merge request if required."""
    if (ctx.settings.rog_enable_comments and
            trigger_comment and
            action.rog_comment_triggers and
            RoGCommentTrigger.JIRA in action.rog_comment_triggers and
            artifact_job.rog):
        issue_url = urllib.parse.urljoin(
            ctx.settings.jira_url, f"/browse/{new_issue.id}")
        rog.add_comment(
            artifact_job.rog.id,
            'New Errata Workflow Automation (NEWA) prepared '
            'a Jira tracker for this merge request.\n\n'
            f'{new_issue.id} - {rendered_summary}\n\n'
            f'{issue_url}')
        ctx.logger.info(
            f"RoG MR {artifact_job.rog.id} was updated "
            f"with a comment about {new_issue.id}")


def _create_jira_job_from_action(
        ctx: CLIContext,
        action: IssueAction,
        artifact_job: ArtifactJob,
        jira_event_fields: dict[str, Any],
        new_issue: Issue) -> None:
    """Create and save JiraJob if action has job_recipe."""
    if action.job_recipe:
        recipe_url = render_template(
            action.job_recipe,
            EVENT=artifact_job.event,
            ERRATUM=artifact_job.erratum,
            COMPOSE=artifact_job.compose,
            JIRA=jira_event_fields,
            ROG=artifact_job.rog,
            CONTEXT=action.context,
            ENVIRONMENT=action.environment)
        if action.erratum_comment_triggers:
            new_issue.erratum_comment_triggers = action.erratum_comment_triggers
        if action.rog_comment_triggers:
            new_issue.rog_comment_triggers = action.rog_comment_triggers
        new_issue.action_id = action.id
        jira_job = JiraJob(
            event=artifact_job.event,
            erratum=artifact_job.erratum,
            compose=artifact_job.compose,
            rog=artifact_job.rog,
            jira=new_issue,
            recipe=Recipe(
                url=recipe_url,
                context=action.context,
                environment=action.environment))
        ctx.save_jira_job(jira_job)


def _close_old_issues(
        ctx: CLIContext,
        old_issues: list[Issue],
        action: IssueAction,
        jira_handler: IssueHandler,
        processed_actions: dict[str, Issue]) -> None:
    """Close old issues that have been replaced."""
    if old_issues:
        if action.on_respin != OnRespinAction.CLOSE:
            raise Exception(
                f"Invalid respin action {action.on_respin} for {old_issues}!")
        # action.id is guaranteed to be non-None due to validation in _process_issue_config
        assert action.id is not None
        for old_issue in old_issues:
            short_sleep()
            jira_handler.drop_obsoleted_issue(
                old_issue, obsoleted_by=processed_actions[action.id])
            ctx.logger.info(f"Old issue {old_issue} closed")


def _expand_action_iterations(
        ctx: CLIContext,
        action: IssueAction,
        issue_actions: list[IssueAction]) -> bool:
    """Expand action iterations if defined. Returns True if iterations were created."""
    if not action.iterate:
        return False

    # For each value prepare a separate action
    for i, iter_vars in enumerate(action.iterate):
        ctx.logger.debug(f"Processing iteration: {iter_vars}")
        new_action = copy.deepcopy(action)
        new_action.iterate = None
        if not new_action.environment:
            new_action.environment = copy.deepcopy(iter_vars)
        else:
            new_action.environment = copy.deepcopy(
                {**new_action.environment, **iter_vars})
        new_action.id = f"{new_action.id}.iter{i + 1}"
        ctx.logger.debug(f"Created issue config action: {new_action}")
        issue_actions.insert(i, new_action)
    ctx.logger.info(f"Created {i} iterations of action {action.id}")
    return True


def _update_action_context_and_environment(
        ctx: CLIContext,
        action: IssueAction) -> None:
    """Update action context and environment with CLI values."""
    if action.context:
        action.context = copy.deepcopy(
            {**action.context, **ctx.cli_context})
    else:
        action.context = copy.deepcopy(ctx.cli_context)
    if action.environment:
        action.environment = copy.deepcopy(
            {**action.environment, **ctx.cli_environment})
    else:
        action.environment = copy.deepcopy(ctx.cli_environment)


def _should_skip_action(
        ctx: CLIContext,
        action: IssueAction,
        artifact_job: ArtifactJob,
        jira_event_fields: dict[str, Any]) -> bool:
    """Check if action should be skipped based on 'when' condition."""
    if action.when and not eval_test(action.when,
                                     JOB=artifact_job,
                                     EVENT=artifact_job.event,
                                     ERRATUM=artifact_job.erratum,
                                     COMPOSE=artifact_job.compose,
                                     JIRA=jira_event_fields,
                                     ROG=artifact_job.rog,
                                     CONTEXT=action.context,
                                     ENVIRONMENT=action.environment):
        ctx.logger.info(f"Skipped, issue action is irrelevant ({action.when})")
        return True
    return False


def _process_issue_action(
        ctx: CLIContext,
        action: IssueAction,
        artifact_job: ArtifactJob,
        jira_handler: IssueHandler,
        config: IssueConfig,
        jira_event_fields: dict[str, Any],
        issue_mapping: dict[str, str],
        no_newa_id: bool,
        recreate: bool,
        assignee: Optional[str],
        unassigned: bool,
        processed_actions: dict[str, Issue],
        created_action_ids: list[str],
        et: Optional[ErrataTool],
        rog: Optional[RoGTool]) -> tuple[Optional[Issue], list[Issue]]:
    """
    Process a single issue action.

    Returns (new_issue, old_issues) or (None, []) if action should be skipped.
    """
    ctx.logger.info(f"Processing {action.id}")

    # Validate action
    if not action.summary:
        raise Exception(f"Action {action} does not have a 'summary' defined.")

    # Render all fields
    (rendered_summary, rendered_description, rendered_assignee,
     rendered_fields, rendered_links, rendered_schedule) = _render_action_fields(
        action, artifact_job, jira_event_fields, assignee, unassigned)

    # Find or create issue
    new_issue, old_issues, trigger_comment = _find_or_create_issue(
        ctx, action, jira_handler, config, issue_mapping,
        no_newa_id, recreate, rendered_summary, rendered_description,
        rendered_assignee, rendered_fields, rendered_links,
        processed_actions, created_action_ids)

    if new_issue is None:
        # Signal to skip this action (closed issues found)
        return None, []

    # Handle erratum comment
    if et:
        _handle_erratum_comment_for_jira(
            ctx, et, artifact_job, action, new_issue,
            rendered_summary, trigger_comment)

    # Handle RoG comment
    if rog:
        _handle_rog_comment_for_jira(
            ctx, rog, artifact_job, action, new_issue,
            rendered_summary, trigger_comment)

    # Create jira job if needed
    # Job is not created for actions with schedule == False, unless
    # we are using action_id_filter_pattern. In such a case, the action
    # is matching a filter, otherwise it would be skipped already
    if rendered_schedule or ctx.action_id_filter_pattern:
        _create_jira_job_from_action(
            ctx, action, artifact_job, jira_event_fields, new_issue)
    else:
        ctx.logger.info(f"Not scheduling action '{action.id}' as requested.")

    # Return issue and old_issues for further processing
    return new_issue, old_issues


def _build_action_id_filtered_list(
        issue_actions: list[IssueAction],
        pattern: Pattern[str]) -> list[str]:
    """ Using the given action.id RegExp Pattern and IssueAction list build a list
    of action IDs matching the pattern and their parent action IDs"""
    # initially populated ids_filtered with action ids matching pattern
    ids_filtered = {
        action.id for action in issue_actions if action.id and pattern.fullmatch(
            action.id)}
    prev_filtered_list_len = -1
    # repeat while filtered list grows
    while len(ids_filtered) > prev_filtered_list_len:
        prev_filtered_list_len = len(ids_filtered)
        # convert actions_filtered to a list and iterate through actions
        # if action has a parent_id, add parent_id to actions_filtered set
        for action in issue_actions:
            if action.id in ids_filtered and action.parent_id:
                ids_filtered.add(action.parent_id)
    return list(ids_filtered)


def _process_issue_config(
        ctx: CLIContext,
        artifact_job: ArtifactJob,
        config: IssueConfig,
        issue_mapping: dict[str, str],
        no_newa_id: bool,
        recreate: bool,
        assignee: Optional[str],
        unassigned: bool,
        jira_handler: IssueHandler,
        et: Optional[ErrataTool],
        rog: Optional[RoGTool]) -> None:
    """Process issue configuration and create/update Jira issues."""
    # All issue actions from the configuration
    issue_actions = config.issues[:]

    action_id_filtered_list: Optional[list[str]] = None
    if ctx.action_id_filter_pattern:
        # only for the issue config processing case we are going to include
        # also parents of actions matching the given id regexp pattern
        action_id_filtered_list = _build_action_id_filtered_list(
            issue_actions, ctx.action_id_filter_pattern)
        ctx.logger.debug(
            f"Filtered action id list including parent ids: {action_id_filtered_list}")

    # Processed actions (action.id : issue)
    processed_actions: dict[str, Issue] = {}

    # action_ids for which new Issues have been created
    created_action_ids: list[str] = []

    # Length of the queue the last time issue action was processed
    endless_loop_check: dict[str, int] = {}

    # Get Jira event fields for Jinja template usage
    jira_event_fields = _get_jira_event_fields(ctx, artifact_job, jira_handler)

    # Iterate over issue actions
    while issue_actions:
        action = issue_actions.pop(0)

        if not action.id:
            raise Exception(f"Action {action} does not have 'id' assigned")

        # Handle iterations
        if _expand_action_iterations(ctx, action, issue_actions):
            continue

        # Check if action.id matches filtered items
        if ctx.skip_action(action.id, action_id_filtered_list):
            continue

        # Update context and environment
        _update_action_context_and_environment(ctx, action)

        # Check 'when' condition
        if _should_skip_action(ctx, action, artifact_job, jira_event_fields):
            continue

        # Check parent availability
        # unless we are using ctx.issue_id_filter_pattern
        if ((not ctx.issue_id_filter_pattern) and action.parent_id
                and action.parent_id not in processed_actions):
            queue_length = len(issue_actions)
            last_queue_length = endless_loop_check.get(action.id, 0)
            if last_queue_length == queue_length:
                raise Exception(f"Parent {action.parent_id} for {action.id} not found!"
                                "It does not exists or is closed.")

            endless_loop_check[action.id] = queue_length
            ctx.logger.info(f"Skipped for now (parent {action.parent_id} not yet found)")
            issue_actions.append(action)
            continue

        # Process the action
        new_issue, old_issues = _process_issue_action(
            ctx, action, artifact_job, jira_handler, config,
            jira_event_fields, issue_mapping, no_newa_id, recreate,
            assignee, unassigned, processed_actions, created_action_ids, et, rog)

        # Skip if issue was closed
        if new_issue is None:
            continue

        # Close old issues if needed
        _close_old_issues(ctx, old_issues, action, jira_handler, processed_actions)


def _get_prev_issue_id(ctx: CLIContext) -> str:
    """Get issue ID from previous state directory."""
    if not ctx.new_state_dir:
        raise Exception(
            "Do not use 'newa -P' or 'newa -D' together with 'jira --prev-issue'")
    if not ctx.prev_state_dirpath:
        raise Exception('Could not identify the previous state-dir')

    ctx_prev = copy.deepcopy(ctx)
    ctx_prev.state_dirpath = ctx.prev_state_dirpath

    jira_jobs = ctx_prev.load_jira_jobs()
    jira_keys = [
        job.jira.id for job in jira_jobs if not job.jira.id.startswith(JIRA_NONE_ID)]

    if len(jira_keys) == 1:
        return jira_keys[0]
    raise Exception(
        f'Expecting a single Jira issue key in {ctx_prev.state_dirpath}, '
        f'found {len(jira_keys)}')


def _create_simple_jira_job(
        ctx: CLIContext,
        artifact_job: ArtifactJob,
        issue: Optional[str],
        prev_issue: bool,
        job_recipe: str,
        jira_none_id: Generator[str, int, None]) -> None:
    """Create a simple JiraJob without using issue-config."""
    if not job_recipe:
        raise Exception("Option --job-recipe is mandatory when --issue-config is not set")

    # Handle prev-issue option
    if prev_issue:
        issue = _get_prev_issue_id(ctx)

    # Handle issue option
    if issue:
        jira_connection = ctx.get_jira_connection()
        jira_issue = jira_connection.get_connection().issue(issue)
        ctx.logger.info(f"Using issue {issue}")
        new_issue = Issue(issue,
                          summary=jira_issue.fields.summary,
                          url=urllib.parse.urljoin(
                              ctx.settings.jira_url, f'/browse/{jira_issue.key}'))
    else:
        # Use an empty string as ID so we skip Jira reporting later
        new_issue = Issue(next(jira_none_id))

    jira_job = JiraJob(event=artifact_job.event,
                       erratum=artifact_job.erratum,
                       compose=artifact_job.compose,
                       rog=artifact_job.rog,
                       jira=new_issue,
                       recipe=Recipe(
                           url=job_recipe,
                           context=ctx.cli_context,
                           environment=ctx.cli_environment))
    ctx.save_jira_job(jira_job)
