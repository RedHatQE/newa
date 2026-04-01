#!/usr/bin/env bash
# Bash completion for newa (New Errata Workflow Automation)
# Source this file or add it to /etc/bash_completion.d/
#
# This completion script dynamically extracts commands and options from
# newa's help output, ensuring it stays in sync with the actual CLI.

_newa_completion() {
    local cur prev opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    # Predefined value completions that can't be extracted from help
    local arch_values="x86_64 aarch64 ppc64le s390x"
    local result_values="passed failed error"

    # Check if we're completing after a specific option that expects a value
    case "${prev}" in
        --state-dir|-D|--extract-state-dir|-E|--conf-file|--issue-config|--job-recipe)
            # Complete with files/directories
            COMPREPLY=( $(compgen -f -- "${cur}") )
            return 0
            ;;
        --arch)
            # Complete with architecture values
            COMPREPLY=( $(compgen -W "${arch_values}" -- "${cur}") )
            return 0
            ;;
        --restart-result|-r)
            # Complete with result values
            COMPREPLY=( $(compgen -W "${result_values}" -- "${cur}") )
            return 0
            ;;
        --workers|--last)
            # Complete with numbers - just return empty to allow user input
            return 0
            ;;
        --erratum|-e|--compose|-c|--jira-issue|--rog-mr|--compose-mapping|--map-issue|--issue|--assignee|--restart-request|-R|--fixture|--action-id-filter|--issue-id-filter|--event-filter|--environment)
            # These expect user-provided values, return empty
            return 0
            ;;
    esac

    # Find which command (if any) has been specified
    # We need to find the LAST command before the current word (for command chaining support)
    local cmd=""
    local all_commands

    # Extract available commands from newa help
    # Look for lines that list commands, typically in format: "  command  Description"
    all_commands=$(newa --help 2>/dev/null | grep -E '^  [a-z]+  ' | awk '{print $1}' | tr '\n' ' ')

    for ((i=1; i < COMP_CWORD; i++)); do
        local word="${COMP_WORDS[i]}"
        # Check if this word is a command (not an option)
        if [[ " ${all_commands} " =~ " ${word} " ]]; then
            cmd="${word}"
            # Don't break - keep looking for more commands (command chaining)
        fi
    done

    # If we're completing the current word and it starts with -, offer options
    if [[ ${cur} == -* ]]; then
        local help_output
        if [[ -n "${cmd}" ]]; then
            # Get help for the specific command
            help_output=$(newa "${cmd}" --help 2>/dev/null)
        else
            # Get main help
            help_output=$(newa --help 2>/dev/null)
        fi

        # Extract options from help output
        # Look for lines with options: "  -s, --long-option" or "  --long-option"
        local options
        options=$(echo "${help_output}" | grep -E '^\s+(-[a-zA-Z]|--[a-z-]+)' | \
                  sed -E 's/.*\s(--[a-z][a-z-]*).*/\1/' | \
                  grep -E '^--' | sort -u | tr '\n' ' ')

        # Also extract short options
        local short_options
        short_options=$(echo "${help_output}" | grep -E '^\s+-[a-zA-Z],' | \
                       sed -E 's/.*\s(-[a-zA-Z]).*/\1/' | \
                       grep -E '^-[a-zA-Z]$' | sort -u | tr '\n' ' ')

        COMPREPLY=( $(compgen -W "${options} ${short_options}" -- "${cur}") )
        return 0
    fi

    # If we're not completing an option, offer commands
    COMPREPLY=( $(compgen -W "${all_commands}" -- "${cur}") )
    return 0
}

# Register the completion function with filenames option
# The -o filenames option tells bash to:
# - Add trailing slashes to directory names
# - Handle spaces in filenames properly
# - Apply standard file completion behavior
complete -o filenames -F _newa_completion newa
