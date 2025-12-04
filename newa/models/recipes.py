"""Recipe and request configuration models."""

import copy
import itertools
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional, TypedDict, Union, cast

try:
    from attrs import define, field
except ModuleNotFoundError:
    from attr import define, field

from newa.models.base import Arch, Cloneable, Serializable
from newa.utils.templates import eval_test, render_template
from newa.utils.yaml_utils import yaml_parser

if TYPE_CHECKING:
    from newa.models.execution import Request

# A tmt context for a recipe, dimension -> value mapping.
RecipeContext = dict[str, str]

# An environment for a recipe, name -> value mapping.
RecipeEnvironment = dict[str, str]


class RawRecipeTmtConfigDimension(TypedDict, total=False):
    url: Optional[str]
    ref: Optional[str]
    path: Optional[str]
    plan: Optional[str]
    plan_filter: Optional[str]
    cli_args: Optional[str]


_RecipeTmtConfigDimensionKey = Literal['url', 'ref', 'path', 'plan', 'plan_filter', 'cli_args']


class RawRecipeTFConfigDimension(TypedDict, total=False):
    cli_args: Optional[str]


_RecipeTFConfigDimensionKey = Literal['cli_args']


ReportPortalAttributes = dict[str, str]


class RawRecipeReportPortalConfigDimension(TypedDict, total=False):
    launch_name: Optional[str]
    launch_description: Optional[str]
    suite_description: Optional[str]
    launch_uuid: Optional[str]
    launch_url: Optional[str]
    launch_attributes: Optional[ReportPortalAttributes]


_RecipeReportPortalConfigDimensionKey = Literal['launch_name',
                                                'launch_description',
                                                'suite_description',
                                                'launch_attributes']


class RawRecipeConfigDimension(TypedDict, total=False):
    context: RecipeContext
    environment: RecipeEnvironment
    compose: Optional[str]
    arch: Optional[Arch]
    tmt: Optional[RawRecipeTmtConfigDimension]
    testingfarm: Optional[RawRecipeTFConfigDimension]
    reportportal: Optional[RawRecipeReportPortalConfigDimension]
    when: Optional[str]


_RecipeConfigDimensionKey = Literal['context', 'environment',
                                    'tmt', 'testingfarm', 'reportportal', 'when', 'arch']


# A list of recipe config dimensions, as stored in a recipe config file.
RawRecipeConfigDimensions = dict[str, list[RawRecipeConfigDimension]]


def _process_dimension_value(
        value: Union[RawRecipeConfigDimension, str],
        jinja_vars: Optional[dict[str, Any]] = None) -> RawRecipeConfigDimension:
    """
    Process a dimension value that can be either a dict or a Jinja2 template string.

    :param value: Either a RawRecipeConfigDimension dict or a Jinja2 template string
    :param jinja_vars: Variables to pass to the Jinja2 template
    :returns: RawRecipeConfigDimension dict
    """
    if isinstance(value, str):
        # Render the Jinja2 template
        rendered = render_template(value, **(jinja_vars or {}))
        # Parse the rendered string as YAML
        parsed = yaml_parser().load(rendered)
        if not isinstance(parsed, dict):
            raise ValueError(
                f"Jinja2 template must render to a YAML dict, got {type(parsed).__name__}")
        return cast(RawRecipeConfigDimension, parsed)
    if isinstance(value, dict):
        return value
    raise ValueError(
        f"Dimension value must be a dict or string, got {type(value).__name__}")


def _process_recipe_data(
        data: dict[str, Any],
        jinja_vars: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """
    Process recipe data to handle Jinja2 template strings in dimension values.

    :param data: Raw recipe data loaded from YAML
    :param jinja_vars: Variables to pass to Jinja2 templates
    :returns: Processed recipe data with all templates rendered
    """
    processed = data.copy()

    # Process fixtures (single RawRecipeConfigDimension)
    if 'fixtures' in processed and isinstance(processed['fixtures'], str):
        processed['fixtures'] = _process_dimension_value(processed['fixtures'], jinja_vars)

    # Process adjustments (list of RawRecipeConfigDimension)
    if 'adjustments' in processed and isinstance(processed['adjustments'], list):
        processed['adjustments'] = [
            _process_dimension_value(item, jinja_vars) for item in processed['adjustments']
            ]

    # Prepare variables for dimension template rendering
    # Merge ENVIRONMENT and CONTEXT from fixtures with CLI values (CLI takes precedence)
    template_vars = {}

    # Start with fixture values
    if 'fixtures' in processed and isinstance(processed['fixtures'], dict):
        if 'environment' in processed['fixtures']:
            template_vars['ENVIRONMENT'] = dict(processed['fixtures']['environment'])
        if 'context' in processed['fixtures']:
            template_vars['CONTEXT'] = dict(processed['fixtures']['context'])

    # Merge/override with CLI values (jinja_vars)
    if jinja_vars:
        if 'ENVIRONMENT' in jinja_vars:
            if 'ENVIRONMENT' in template_vars:
                template_vars['ENVIRONMENT'].update(jinja_vars['ENVIRONMENT'])
            else:
                template_vars['ENVIRONMENT'] = copy.deepcopy(jinja_vars['ENVIRONMENT'])
        if 'CONTEXT' in jinja_vars:
            if 'CONTEXT' in template_vars:
                template_vars['CONTEXT'].update(jinja_vars['CONTEXT'])
            else:
                template_vars['CONTEXT'] = copy.deepcopy(jinja_vars['CONTEXT'])
        # Add any other jinja_vars that are not ENVIRONMENT or CONTEXT
        for key, value in jinja_vars.items():
            if key not in ('ENVIRONMENT', 'CONTEXT'):
                template_vars[key] = copy.deepcopy(value)

    # Process dimensions (dict[str, list[RawRecipeConfigDimension]])
    if 'dimensions' in processed and isinstance(processed['dimensions'], dict):
        processed_dimensions = {}
        for dimension_name, dimension_list in processed['dimensions'].items():
            if isinstance(dimension_list, str):
                # The entire list is a Jinja2 template string
                rendered = render_template(
                    dimension_list, **template_vars)  # type: ignore[arg-type]
                parsed = yaml_parser().load(rendered)
                if not isinstance(parsed, list):
                    msg = (
                        f"Jinja2 template for dimension '{dimension_name}' "
                        f"must render to a YAML list, got {type(parsed).__name__}"
                        )
                    raise ValueError(msg)
                # Process each item in the parsed list
                processed_dimensions[dimension_name] = [
                    _process_dimension_value(item, template_vars) for item in parsed
                    ]
            elif isinstance(dimension_list, list):
                # Process each item in the list (may contain dicts or strings)
                processed_dimensions[dimension_name] = [
                    _process_dimension_value(item, template_vars) for item in dimension_list
                    ]
            else:
                processed_dimensions[dimension_name] = dimension_list
        processed['dimensions'] = processed_dimensions

    return processed


@define
class Recipe(Cloneable, Serializable):
    """A job recipe"""

    url: str
    context: Optional[RecipeContext] = None
    environment: Optional[RecipeEnvironment] = None


@define
class RecipeConfig(Cloneable, Serializable):
    """A job recipe configuration"""

    fixtures: RawRecipeConfigDimension = field(
        factory=cast(Callable[[], RawRecipeConfigDimension], dict))
    adjustments: list[RawRecipeConfigDimension] = field(
        factory=cast(Callable[[], list[RawRecipeConfigDimension]], list))
    dimensions: RawRecipeConfigDimensions = field(
        factory=cast(Callable[[], RawRecipeConfigDimensions], dict))
    includes: list[str] = field(factory=list)

    @classmethod
    def from_yaml(
            cls: type['RecipeConfig'],
            serialized: str,
            jinja_vars: Optional[dict[str, Any]] = None) -> 'RecipeConfig':
        """
        Load RecipeConfig from YAML string, processing Jinja2 templates in dimension values.

        :param serialized: YAML string to parse
        :param jinja_vars: Variables to pass to Jinja2 templates
        :returns: RecipeConfig instance
        """
        data = yaml_parser().load(serialized)
        processed_data = _process_recipe_data(data, jinja_vars)
        return cls(**processed_data)

    @classmethod
    def from_yaml_with_includes(
            cls: type['RecipeConfig'],
            location: str,
            stack: Optional[list[str]] = None) -> 'RecipeConfig':
        import re
        base_config = cls.from_yaml_url(location) if \
            re.search('^https?://', location) else cls.from_yaml_file(Path(location))
        # process each include
        fixtures_combination = []
        adjustments = []
        for source in base_config.includes:
            if stack and source in stack:
                raise Exception(
                    f'Duplicate location encountered while loading recipe YAML from "{location}"')
            if stack:
                stack.append(location)
            else:
                stack = [location]
            source_config = cls.from_yaml_with_includes(source, stack=stack)
            if source_config.fixtures:
                fixtures_combination.append(source_config.fixtures)
            if source_config.adjustments:
                adjustments.extend(source_config.adjustments)
        if base_config.fixtures:
            fixtures_combination.append(base_config.fixtures)
        if base_config.adjustments:
            adjustments.extend(base_config.adjustments)
        if len(fixtures_combination) > 1:
            merged_fixtures = base_config.merge_combination_data(tuple(fixtures_combination))
            base_config.fixtures = copy.deepcopy(merged_fixtures)
        base_config.adjustments = copy.deepcopy(adjustments)
        return base_config

    def merge_combination_data(
            self, combination: tuple[RawRecipeConfigDimension, ...]) -> RawRecipeConfigDimension:

        # Note: moved into its own function to avoid being indented too much;
        # mypy needs to be silenced because we use `key` variable instead of
        # literal keys defined in the corresponding typeddicts. And being nested
        # too much, autopep8 was reformatting and misplacing `type: ignore`.
        def _merge_key(
                dest: RawRecipeConfigDimension,
                src: RawRecipeConfigDimension,
                key: str) -> None:
            # instruct how individual attributes should be merged
            # attribute 'when' needs special treatment as we are joining conditions with 'and'
            if key == 'when' and ("when" not in dest) and src["when"]:
                dest['when'] = f'( {src["when"]} )'
            elif key == 'when' and dest["when"] and src["when"]:
                dest['when'] += f' and ( {src["when"]} )'
            elif key not in dest:
                # we need to do a deep copy so we won't corrupt the original data
                dest[key] = copy.deepcopy(src[key])  # type: ignore[literal-required]
            elif isinstance(dest[key], dict) and isinstance(src[key], dict):  # type: ignore[literal-required]
                dest[key].update(src[key])  # type: ignore[literal-required]
            elif isinstance(dest[key], list) and isinstance(src[key], list):  # type: ignore[literal-required]
                dest[key].extend(src[key])  # type: ignore[literal-required]
            elif isinstance(dest[key], str) and isinstance(src[key], str):  # type: ignore[literal-required]
                dest[key] = src[key]  # type: ignore[literal-required]
            else:
                raise Exception(f"Don't know how to merge record type '{key}'")

        merged: RawRecipeConfigDimension = {}
        for record in combination:
            for key in record:
                _merge_key(merged, record, key)
        return merged

    def build_requests(self,
                       initial_config: RawRecipeConfigDimension,
                       cli_config: RawRecipeConfigDimension,
                       jinja_vars: Optional[dict[str, Any]] = None) -> Iterator['Request']:
        from newa.models.artifacts import Compose
        from newa.models.execution import Request, gen_global_request_counter

        # cli_config has a priority while initial_config can be modified by a recipe

        # this is here to generate unique recipe IDs
        recipe_id_gen = itertools.count(start=1)

        # get all options from dimensions
        options: list[list[RawRecipeConfigDimension]] = [self.dimensions[dimension]
                                                         for dimension in self.dimensions]
        # extend options with adjustments
        for adjustment in self.adjustments:
            # if 'when' rule is present, we need to provide an empty alternative with
            # negated condition not to cancel other options when condition is not match
            if 'when' in adjustment:
                options.append([adjustment, {'when': f'not ({adjustment["when"]})'}])
            else:
                options.append([adjustment])
        # generate combinations
        combinations = list(itertools.product(*options))
        # extend each combination with initial_config, fixtures and cli_config
        for i in range(len(combinations)):
            combinations[i] = (initial_config, ) + (self.fixtures,) + \
                combinations[i] + (cli_config, )

        # now for each combination merge data from individual dimensions
        merged_combinations = list(map(self.merge_combination_data, combinations))
        # and filter them evaluating 'when' conditions
        filtered_combinations = []
        for combination in merged_combinations:
            # check if there is a condition present and evaluate it
            condition = combination.get('when', '')
            if condition:
                compose: Optional[str] = combination.get('compose', '')
                # we will expose COMPOSE, ENVIRONMENT, CONTEXT to evaluate a condition
                arch = combination.get('arch', None)
                test_result = eval_test(
                    condition,
                    COMPOSE=Compose(compose) if compose else None,
                    ARCH=arch.value if arch else None,
                    ENVIRONMENT=combination.get('environment', None),
                    CONTEXT=combination.get('context', None),
                    **(jinja_vars if jinja_vars else {}))
                if not test_result:
                    continue
            filtered_combinations.append(combination)
        # now build Request instances
        total = len(filtered_combinations)
        for combination in filtered_combinations:
            yield Request(
                id=f'REQ-{next(recipe_id_gen)}.{total}.{next(gen_global_request_counter)}',
                **combination)
