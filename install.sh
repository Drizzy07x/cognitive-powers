#!/usr/bin/env bash
# POSIX-shell port of install.ps1 for Linux and macOS.
#
# Behaviour is intended to be identical: the same preflight order, the same
# fail-closed marketplace checks, the same transactional install with recovery,
# and the same postcondition verification. Where PowerShell supplies something
# the shell does not, the divergence is marked with "DIVERGENCE" and says what
# was substituted and why the substitution is safe.
#
# DIVERGENCE (shell dialect): this is bash, not pure POSIX sh. It uses [[ =~ ]]
# for the same regular expressions install.ps1 applies with -match, and holds to
# constructs available in bash 3.2, which is what macOS still ships -- no
# associative arrays, no mapfile, no ${var,,}. Empty indexed arrays are avoided
# entirely rather than guarded, because expanding one under `set -u` is an error
# in exactly the bash versions this has to run on.
#
# DIVERGENCE (exit codes): install.ps1 reports every failure by throwing, and
# `pwsh -File` turns an uncaught throw into exit 1. Every failure here exits 1
# for the same reason, so a caller cannot tell the two scripts apart by status.

set -euo pipefail

release_ref="v1.10.0-rc.3"

repository="Drizzy07x/cognitive-powers"
marketplace="cognitive-powers"
personal_marketplace_name="personal"
plugin_id="cognitive-powers@cognitive-powers"
personal_plugin_id="cognitive-powers@personal"
plugin_name="cognitive-powers"

# BASH_SOURCE is unset when the script is read from stdin, and `set -u` turns
# that into an abort before the first useful message. $0 is the documented
# fallback, and the canonical verifier is resolved from here for the same reason
# install.ps1 resolves it from $PSScriptRoot: it ships beside the script, so a
# copy run from outside its checkout has no verifier to run.
script_root="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

# The default is printed from the variable, never spelled again here: a second
# copy of the tag is a second thing for bump_version.py to miss, and the help
# text is where nobody would notice it had gone stale.
usage() {
    cat >&2 <<USAGE
usage: install.sh [--release-ref vX.Y.Z]

  --release-ref, -ReleaseRef   release tag to install (default: $release_ref)

The -ReleaseRef spelling is accepted so the documented command is identical on
every host; --release-ref is the POSIX spelling of the same option.
USAGE
}

# DIVERGENCE (parameter binding): install.ps1 declares [ValidatePattern] on the
# parameter, so PowerShell rejects a malformed ref before the body runs. The
# shell has no such binding, so the same pattern is applied here by hand -- and
# it has to stay applied, because $expected_version is derived from this string
# and a ref that is not vX.Y.Z would silently verify against nonsense.
while [ "$#" -gt 0 ]; do
    case "$1" in
        --release-ref|-ReleaseRef)
            if [ "$#" -lt 2 ]; then
                printf 'install.sh: %s requires a value.\n' "$1" >&2
                exit 1
            fi
            release_ref="$2"
            shift 2
            ;;
        --release-ref=*)
            release_ref="${1#*=}"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'install.sh: unrecognized argument %s\n' "$1" >&2
            usage
            exit 1
            ;;
    esac
done

if [[ ! "$release_ref" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-(alpha|beta|rc)\.[0-9]+)?$ ]]; then
    printf "install.sh: release ref '%s' is not of the form vX.Y.Z or vX.Y.Z-rc.N.\\n" "$release_ref" >&2
    exit 1
fi
expected_version="${release_ref#v}"

allowed_sources="$repository
$repository@$release_ref
https://github.com/$repository
https://github.com/$repository.git
git@github.com:$repository
git@github.com:$repository.git
ssh://git@github.com/$repository
ssh://git@github.com/$repository.git"

die() {
    printf 'install.sh: %s\n' "$1" >&2
    exit 1
}

assert_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        die "Required command '$1' was not found in PATH."
    fi
}

# DIVERGENCE (interpreter name): install.ps1 asks for "python", which is the
# name that exists on Windows. PEP 394 makes "python3" the name that exists on
# Linux and macOS, and on many distributions a bare "python" is absent
# altogether, so hardcoding it would fail the preflight for almost every user of
# this script. The search order is explicit override, python3, python.
resolve_python() {
    local candidate
    # Checked on its own rather than folded into the loop word list: an
    # interpreter path containing a space is exactly the case an override
    # exists for, and unquoted expansion would split it into two candidates
    # that both fail to resolve.
    if [ -n "${COGNITIVE_POWERS_PYTHON:-}" ]; then
        if command -v "$COGNITIVE_POWERS_PYTHON" >/dev/null 2>&1; then
            printf '%s' "$COGNITIVE_POWERS_PYTHON"
            return 0
        fi
        return 1
    fi
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

assert_python() {
    # The Windows Store stub this guards against on the PowerShell side has no
    # Unix counterpart, but running the interpreter still has to happen here and
    # in this position. Every JSON document below is parsed with it, and the
    # canonical verifier at the very end is the only other user -- so an
    # interpreter that resolves without running would otherwise be discovered
    # after the profile was mutated, and reported as a rollback rather than as a
    # missing interpreter.
    local status=0
    "$python_command" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 3)' || status=$?
    if [ "$status" -eq 3 ]; then
        die "Cognitive Powers requires Python 3.11 or newer; '$python_command' reports an older version. Install a newer interpreter and verify with '$python_command --version'."
    fi
    if [ "$status" -ne 0 ]; then
        die "Required command '$python_command' resolves but does not run (exit code $status). Install Python 3.11 or newer, or set COGNITIVE_POWERS_PYTHON to a working interpreter, then verify with '$python_command --version'."
    fi
}

run_checked() {
    # The status has to be captured from the command itself. Reading $? inside
    # `if ! "$@"; then` reports the status of the negation, which is 0 on the
    # failing branch -- the check would then report success as the reason for
    # the failure it just caught.
    local status=0
    "$@" || status=$?
    if [ "$status" -ne 0 ]; then
        printf 'Command failed with exit code %s: %s\n' "$status" "$*" >&2
        return "$status"
    fi
}

codex_best_effort() {
    codex "$@" >/dev/null 2>&1
}

# DIVERGENCE (JSON): PowerShell has ConvertFrom-Json; the shell has no JSON
# parser, and neither jq nor python is guaranteed on a bare host. Parsing these
# documents with sed would be the fragile option, so the already-verified
# interpreter does it -- assert_python runs before the first parse for exactly
# this reason, in the same position install.ps1 puts it.
#
# Records come back tab-separated, and a value containing a tab or a newline is
# refused rather than split into fields that mean something else. That is the
# fail-closed reading of a value nobody expects to see.
#
# The program is held in a variable and passed with -c rather than fed on
# stdin: stdin is where the JSON document arrives, and a heredoc there would
# hand the parser its own source to parse.
json_helper=$(cat <<'PYTHON'
import json
import sys


def field(value):
    text = "" if value is None else str(value)
    if "\t" in text or "\n" in text or "\r" in text:
        raise SystemExit(3)
    return text


def flag(value):
    return "true" if value is True else "false"


query = sys.argv[1]
try:
    document = json.load(sys.stdin)
except (ValueError, UnicodeDecodeError):
    raise SystemExit(2)
if not isinstance(document, dict):
    raise SystemExit(2)

rows = []
if query == "sha":
    rows.append([field(document.get("sha"))])
elif query == "marketplaces":
    entries = document.get("marketplaces")
    if not isinstance(entries, list):
        raise SystemExit(2)
    for entry in entries:
        if not isinstance(entry, dict):
            raise SystemExit(2)
        source = entry.get("marketplaceSource")
        source = source.get("source") if isinstance(source, dict) else None
        rows.append(
            [field(entry.get("name")), field(entry.get("root")), field(source)]
        )
elif query == "installed":
    entries = document.get("installed")
    if not isinstance(entries, list):
        raise SystemExit(2)
    for entry in entries:
        if not isinstance(entry, dict):
            raise SystemExit(2)
        rows.append(
            [
                field(entry.get("name")),
                field(entry.get("pluginId")),
                flag(entry.get("installed")),
                flag(entry.get("enabled")),
                field(entry.get("version")),
            ]
        )
else:
    raise SystemExit(2)

# Written as bytes, not text. Text mode translates "\n" on the way out on some
# hosts, and the shell's `read` strips only the newline -- so the carriage
# return stayed on the end of the last field and every comparison against it
# failed against a value that printed as though it were correct.
sys.stdout.buffer.write(
    "".join("\t".join(row) + "\n" for row in rows).encode("utf-8")
)
PYTHON
)

json_query() {
    "$python_command" -c "$json_helper" "$1"
}

# DIVERGENCE (path canonicalization): install.ps1 uses [IO.Path]::GetFullPath,
# which normalizes lexically and leaves symlinks alone. `realpath -m` is the
# obvious substitute and is GNU-only -- macOS does not have it -- so this uses
# `cd` plus `pwd -P`, which is POSIX and resolves symlinks. Resolving is the
# behaviour worth having here: on these platforms $TMPDIR and $HOME are
# routinely symlinks, and a lexical compare reports a real match as a mismatch.
# Both sides of every comparison go through this, so resolving can only prevent
# a false mismatch, never manufacture a false match.
#
# The interpreter is deliberately not used for this. It would have to be, if the
# rule were GetFullPath's, but a path is the one value where the interpreter's
# idea of a filesystem and the shell's can disagree -- and the disagreement is
# silent, because both return a string that looks right.
canonical_path() {
    local target="$1" directory base
    directory="$(dirname "$target")"
    base="$(basename "$target")"
    if [ -d "$directory" ]; then
        printf '%s/%s' "$(cd "$directory" && pwd -P)" "$base"
        return 0
    fi
    # A path whose parent does not exist cannot be resolved, only cleaned up.
    # GetFullPath answers lexically in that case too, and every caller compares
    # the result against a path that does exist, so an unresolvable one fails
    # the comparison rather than matching something it should not.
    printf '%s' "$target"
}

python_command="$(resolve_python || true)"
if [ -z "$python_command" ]; then
    die "Required command 'python3' was not found in PATH."
fi

verifier="$script_root/scripts/verify_installed.py"

assert_verifier() {
    # Checked here rather than at the point of use, for the same reason
    # assert_python runs here: the postcondition needs a file that ships beside
    # this script, its absence is knowable before anything is touched, and
    # discovered at the end instead it is reported as a failed installation and
    # a rollback rather than as a missing file.
    if [ ! -f "$verifier" ]; then
        die "The canonical verifier is missing at '$verifier'. Run install.sh from a complete checkout of the release."
    fi
}

assert_command "gh"
assert_command "codex"
assert_python
assert_verifier
run_checked gh auth status --hostname github.com || die "Command failed: gh auth status"
run_checked gh auth setup-git --hostname github.com || die "Command failed: gh auth setup-git"
run_checked gh api "repos/$repository/git/ref/tags/$release_ref" --silent ||
    die "Command failed: gh api repos/$repository/git/ref/tags/$release_ref"

release_commit_raw=""
if ! release_commit_raw="$(gh api "repos/$repository/commits/$release_ref")"; then
    die "Unable to resolve immutable release commit for '$release_ref'."
fi
release_commit=""
if ! release_commit="$(printf '%s' "$release_commit_raw" | json_query sha)"; then
    die "GitHub returned invalid JSON while resolving '$release_ref'."
fi
release_commit="${release_commit%$'\n'}"
if [[ ! "$release_commit" =~ ^[0-9a-f]{40}$ ]]; then
    die "Release '$release_ref' did not resolve to a full commit SHA."
fi

marketplace_state_raw=""
if ! marketplace_state_raw="$(codex plugin marketplace list --json)"; then
    die "Unable to read configured Codex marketplaces."
fi
marketplace_rows=""
if ! marketplace_rows="$(printf '%s' "$marketplace_state_raw" | json_query marketplaces)"; then
    die "Unable to read configured Codex marketplaces."
fi

configured_count=0
configured_root=""
configured_source=""
personal_count=0
personal_root=""
while IFS=$'\t' read -r row_name row_root row_source; do
    [ -n "$row_name" ] || continue
    if [ "$row_name" = "$marketplace" ]; then
        configured_count=$((configured_count + 1))
        configured_root="$row_root"
        configured_source="$row_source"
    elif [ "$row_name" = "$personal_marketplace_name" ]; then
        personal_count=$((personal_count + 1))
        personal_root="$row_root"
    fi
done <<< "$marketplace_rows"

if [ "$configured_count" -gt 1 ]; then
    die "More than one marketplace named '$marketplace' is configured."
fi

# DIVERGENCE (profile location): install.ps1's Get-RecoveryParent spells this
# same rule out for every host except Windows, so the two scripts stay pointed
# at one directory and a recovery marketplace written by either is recognized by
# both. It used to leave the answer to .NET's LocalApplicationData, which
# matches this rule only on Linux: on macOS that is Library/Application Support
# under the account's own home and consults neither variable, so each installer
# refused to resume from the other's recovery copy. mkdir -p reproduces the
# "Create" the .NET call used to do, because a profile that has never been
# written to has no ~/.local/share yet and the recovery copy has to live
# somewhere.
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
recovery_parent="$data_home/cognitive-powers"
# Created here rather than at the point of use, because the recognition check
# below canonicalizes this path and comparing an unresolvable path against a
# resolved one is a mismatch whatever the two actually name. GetFolderPath's
# "Create" argument materializes the same directory at the same point.
mkdir -p "$recovery_parent"

if [ "$configured_count" -eq 1 ]; then
    configured_source_is_pinned_repository=0
    if [[ "$configured_source" =~ ^${repository}@(v[0-9]+\.[0-9]+\.[0-9]+(-(alpha|beta|rc)\.[0-9]+)?|[0-9a-f]{40})$ ]]; then
        configured_source_is_pinned_repository=1
    fi

    # A failed transaction restores the previous installation from a recovery
    # marketplace under the data home and preserves it. That state is this
    # installer's own product, so a rerun must recognize it and proceed --
    # re-pointing the marketplace at the new immutable SHA -- instead of
    # refusing the very recovery it created. Recognition is deliberately
    # narrow: the exact directory shape the transaction writes, nothing else.
    configured_source_is_recovery_marketplace=0
    if [ -n "$configured_source" ] &&
        [[ "$configured_source" != *"://"* ]] &&
        [ "${configured_source#/}" != "$configured_source" ]; then
        full_source="$(canonical_path "$configured_source")"
        source_leaf="$(basename "$full_source")"
        rollback_directory="$(dirname "$full_source")"
        rollback_leaf="$(basename "$rollback_directory")"
        rollback_parent="$(dirname "$rollback_directory")"
        if [ "$source_leaf" = "marketplace" ] &&
            [[ "$rollback_leaf" =~ ^rollback-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] &&
            [ "$rollback_parent" = "$(canonical_path "$recovery_parent")" ] &&
            [ -f "$full_source/.agents/plugins/marketplace.json" ]; then
            configured_source_is_recovery_marketplace=1
        fi
    fi

    source_is_allowed=0
    while IFS= read -r candidate; do
        if [ -n "$configured_source" ] && [ "$candidate" = "$configured_source" ]; then
            source_is_allowed=1
        fi
    done <<< "$allowed_sources"

    if [ -z "${configured_source// /}" ] ||
        {
            [ "$source_is_allowed" -eq 0 ] &&
                [ "$configured_source_is_pinned_repository" -eq 0 ] &&
                [ "$configured_source_is_recovery_marketplace" -eq 0 ]
        }; then
        die "Marketplace '$marketplace' already points to '$configured_source', not '$repository'."
    fi
fi

pre_install_raw=""
if ! pre_install_raw="$(codex plugin list --json)"; then
    die "Unable to inspect existing Codex plugins."
fi
installed_rows=""
if ! installed_rows="$(printf '%s' "$pre_install_raw" | json_query installed)"; then
    die "Unable to inspect existing Codex plugins."
fi

# Duplicates are kept as tab-separated text rather than an array so that the
# empty case needs no guard: expanding an empty indexed array under `set -u` is
# an error on the bash macOS ships, and "no prior installation" is the common
# case rather than an edge one.
duplicates=""
duplicate_count=0
while IFS=$'\t' read -r row_name row_plugin_id row_installed row_enabled row_version; do
    [ -n "$row_name" ] || continue
    [ "$row_name" = "$plugin_name" ] || continue
    [ "$row_installed" = "true" ] || continue
    duplicates+="$row_plugin_id"$'\t'"$row_enabled"$'\t'"$row_version"$'\n'
    duplicate_count=$((duplicate_count + 1))
done <<< "$installed_rows"

private_previous_count=0
personal_previous_count=0
seen_plugin_ids=""
if [ "$duplicate_count" -gt 0 ]; then
    while IFS=$'\t' read -r row_plugin_id row_enabled row_version; do
        [ -n "$row_plugin_id" ] || continue
        if [ "$row_plugin_id" != "$plugin_id" ] && [ "$row_plugin_id" != "$personal_plugin_id" ]; then
            die "An unrecognized Cognitive Powers installation cannot be restored safely."
        fi
        case "$seen_plugin_ids" in
            *"|$row_plugin_id|"*)
                die "Duplicate plugin identifiers cannot be restored unambiguously."
                ;;
        esac
        seen_plugin_ids+="|$row_plugin_id|"
        if [ "$row_enabled" != "true" ]; then
            die "A disabled prior installation cannot be restored exactly; refusing to mutate it."
        fi
        if [ "$row_plugin_id" = "$plugin_id" ]; then
            private_previous_count=$((private_previous_count + 1))
        else
            personal_previous_count=$((personal_previous_count + 1))
        fi
    done <<< "$duplicates"
fi

previous_release_commit=""
if [ "$configured_count" -eq 1 ]; then
    if [[ "$configured_source" =~ ^${repository}@([0-9a-f]{40})$ ]]; then
        previous_release_commit="${BASH_REMATCH[1]}"
    elif [[ "$configured_source" =~ ^${repository}@(v[0-9]+\.[0-9]+\.[0-9]+(-(alpha|beta|rc)\.[0-9]+)?)$ ]]; then
        previous_ref="${BASH_REMATCH[1]}"
        previous_commit_raw=""
        if ! previous_commit_raw="$(gh api "repos/$repository/commits/$previous_ref")"; then
            die "Unable to resolve the previous immutable release '$previous_ref'."
        fi
        if ! previous_release_commit="$(printf '%s' "$previous_commit_raw" | json_query sha)"; then
            die "GitHub returned invalid JSON while resolving the previous release."
        fi
        previous_release_commit="${previous_release_commit%$'\n'}"
    elif [ -n "$configured_root" ]; then
        previous_release_commit="$(git -C "$configured_root" rev-parse HEAD 2>/dev/null || true)"
        previous_release_commit="$(printf '%s' "$previous_release_commit" | tr -d '[:space:]')"
    fi
    if [[ ! "$previous_release_commit" =~ ^[0-9a-f]{40}$ ]]; then
        die "The previous marketplace cannot be bound to an immutable commit; refusing to mutate it."
    fi
fi

if [ "$private_previous_count" -ne 0 ] && [ "$configured_count" -ne 1 ]; then
    die "The private plugin has no configured marketplace to back up; refusing to mutate it."
fi
if [ "$personal_previous_count" -ne 0 ]; then
    if [ "$personal_count" -ne 1 ] || [ -z "${personal_root// /}" ] || [ ! -d "$personal_root" ]; then
        die "The personal plugin marketplace is unavailable; refusing to mutate it."
    fi
fi

# DIVERGENCE (identifier): [guid]::NewGuid() has no portable shell equivalent --
# uuidgen is absent on minimal Linux images and /proc is absent on macOS -- so
# the verified interpreter produces it. The spelling has to keep matching the
# recovery-marketplace pattern above, or a preserved recovery would stop being
# recognized on the next run.
rollback_root="$recovery_parent/rollback-$("$python_command" -c 'import uuid; print(uuid.uuid4())')"
rollback_marketplace="$rollback_root/marketplace"
rollback_prepared=0
preserve_rollback=0
mutation_started=0
transaction_error=""

# DIVERGENCE (finally): PowerShell's finally block runs on every exit path. A
# trap on EXIT is the equivalent, and it has to be installed before the first
# mutation so that an interrupt cannot leave the recovery copy behind when the
# transaction never asked for it to be preserved.
cleanup() {
    if [ -d "$rollback_root" ] && [ "$preserve_rollback" -eq 0 ]; then
        rm -rf "$rollback_root"
    fi
}
trap cleanup EXIT

transact() {
    if [ "$configured_count" -eq 1 ]; then
        if [ -z "${configured_root// /}" ] || [ ! -d "$configured_root" ]; then
            transaction_error="Configured marketplace root is unavailable; refusing to mutate the installation."
            return 1
        fi
        mkdir -p "$rollback_root"
        # cp -R of a directory onto a non-existent destination copies the
        # directory itself, which is what Copy-Item -Recurse does here.
        if ! cp -R "$configured_root" "$rollback_marketplace"; then
            transaction_error="Marketplace rollback copy could not be created; refusing to mutate the installation."
            return 1
        fi
        if [ ! -f "$rollback_marketplace/.agents/plugins/marketplace.json" ]; then
            transaction_error="Marketplace rollback copy is incomplete; refusing to mutate the installation."
            return 1
        fi
        rollback_prepared=1
    fi

    if [ "$private_previous_count" -ne 0 ]; then
        mutation_started=1
        if ! run_checked codex plugin remove "$plugin_id" --json; then
            transaction_error="Removing the previous private plugin failed."
            return 1
        fi
    fi
    if [ "$configured_count" -eq 1 ]; then
        mutation_started=1
        if ! run_checked codex plugin marketplace remove "$marketplace" --json; then
            transaction_error="Removing the configured marketplace failed."
            return 1
        fi
    fi
    mutation_started=1
    if ! run_checked codex plugin marketplace add "$repository" --ref "$release_commit" --json; then
        transaction_error="Adding the pinned marketplace failed."
        return 1
    fi
    if ! run_checked codex plugin add "$plugin_id" --json; then
        transaction_error="Adding the private plugin failed."
        return 1
    fi

    local provisional_raw provisional_rows provisional_matches=0
    if ! provisional_raw="$(codex plugin list --json)"; then
        transaction_error="Unable to verify the provisional installation."
        return 1
    fi
    if ! provisional_rows="$(printf '%s' "$provisional_raw" | json_query installed)"; then
        transaction_error="Unable to verify the provisional installation."
        return 1
    fi
    while IFS=$'\t' read -r row_name row_plugin_id row_installed row_enabled row_version; do
        [ -n "$row_plugin_id" ] || continue
        if [ "$row_plugin_id" = "$plugin_id" ] && [ "$row_installed" = "true" ] &&
            [ "$row_enabled" = "true" ] && [ "$row_version" = "$expected_version" ]; then
            provisional_matches=$((provisional_matches + 1))
        fi
    done <<< "$provisional_rows"
    if [ "$provisional_matches" -ne 1 ]; then
        transaction_error="The provisional private installation is invalid."
        return 1
    fi

    if [ "$personal_previous_count" -ne 0 ]; then
        if ! run_checked codex plugin remove "$personal_plugin_id" --json; then
            transaction_error="Removing the previous personal plugin failed."
            return 1
        fi
    fi

    local plugin_raw plugin_rows enabled_count=0 enabled_plugin_id="" enabled_version=""
    if ! plugin_raw="$(codex plugin list --json)"; then
        transaction_error="Unable to verify installed Codex plugins."
        return 1
    fi
    if ! plugin_rows="$(printf '%s' "$plugin_raw" | json_query installed)"; then
        transaction_error="Unable to verify installed Codex plugins."
        return 1
    fi
    while IFS=$'\t' read -r row_name row_plugin_id row_installed row_enabled row_version; do
        [ -n "$row_name" ] || continue
        if [ "$row_name" = "$plugin_name" ] && [ "$row_installed" = "true" ] &&
            [ "$row_enabled" = "true" ]; then
            enabled_count=$((enabled_count + 1))
            enabled_plugin_id="$row_plugin_id"
            enabled_version="$row_version"
        fi
    done <<< "$plugin_rows"
    if [ "$enabled_count" -ne 1 ] || [ "$enabled_plugin_id" != "$plugin_id" ] ||
        [ "$enabled_version" != "$expected_version" ]; then
        transaction_error="Expected exactly one enabled '$plugin_name' plugin at version '$expected_version': '$plugin_id'."
        return 1
    fi

    local installed_marketplace_raw installed_marketplace_rows
    local installed_count=0 installed_root=""
    if ! installed_marketplace_raw="$(codex plugin marketplace list --json)"; then
        transaction_error="Unable to resolve the installed marketplace root."
        return 1
    fi
    if ! installed_marketplace_rows="$(printf '%s' "$installed_marketplace_raw" | json_query marketplaces)"; then
        transaction_error="Unable to resolve the installed marketplace root."
        return 1
    fi
    while IFS=$'\t' read -r row_name row_root row_source; do
        [ -n "$row_name" ] || continue
        if [ "$row_name" = "$marketplace" ]; then
            installed_count=$((installed_count + 1))
            installed_root="$row_root"
        fi
    done <<< "$installed_marketplace_rows"
    if [ "$installed_count" -ne 1 ] || [ -z "${installed_root// /}" ]; then
        transaction_error="Expected exactly one installed marketplace root for '$marketplace'."
        return 1
    fi
    installed_root="$(canonical_path "$installed_root")"

    if ! run_checked "$python_command" "$verifier" \
        --source-root "$installed_root" \
        --installed-root "$installed_root" \
        --tag "$release_ref"; then
        transaction_error="The canonical installed-copy verifier rejected the installation."
        return 1
    fi
}

# rollback_succeeded and preserve_rollback are globals written here rather than
# a value printed for the caller to read. A command substitution runs the
# function in a subshell, so preserve_rollback would be set in a process that
# exits immediately after -- and the EXIT trap in the parent would then delete
# the recovery marketplace the failure message tells the operator to keep.
rollback_succeeded=1
rollback() {
    local restored_from_remote=0
    if [ "$mutation_started" -eq 0 ]; then
        return 0
    fi

    codex_best_effort plugin remove "$plugin_id" --json || true
    local target_marketplace_removed=0
    if codex_best_effort plugin marketplace remove "$marketplace" --json; then
        target_marketplace_removed=1
    fi
    if [ "$configured_count" -eq 1 ] && [ -n "$previous_release_commit" ] &&
        [ "$target_marketplace_removed" -eq 1 ]; then
        if codex_best_effort plugin marketplace add "$repository" \
            --ref "$previous_release_commit" --json; then
            restored_from_remote=1
        fi
    fi
    if [ "$configured_count" -eq 1 ] && [ "$rollback_prepared" -eq 1 ] &&
        [ "$restored_from_remote" -eq 0 ]; then
        codex_best_effort plugin marketplace add "$rollback_marketplace" --json || true
    fi
    if [ "$duplicate_count" -gt 0 ]; then
        while IFS=$'\t' read -r row_plugin_id row_enabled row_version; do
            [ -n "$row_plugin_id" ] || continue
            codex_best_effort plugin add "$row_plugin_id" --json || true
        done <<< "$duplicates"
    fi

    local restored_raw restored_rows
    if ! restored_raw="$(codex plugin list --json 2>/dev/null)" ||
        ! restored_rows="$(printf '%s' "$restored_raw" | json_query installed 2>/dev/null)"; then
        rollback_succeeded=0
    else
        local restored_count=0
        while IFS=$'\t' read -r row_name row_plugin_id row_installed row_enabled row_version; do
            [ -n "$row_name" ] || continue
            if [ "$row_name" = "$plugin_name" ] && [ "$row_installed" = "true" ]; then
                restored_count=$((restored_count + 1))
            fi
        done <<< "$restored_rows"
        if [ "$restored_count" -ne "$duplicate_count" ]; then
            rollback_succeeded=0
        fi
        if [ "$duplicate_count" -gt 0 ]; then
            while IFS=$'\t' read -r want_plugin_id want_enabled want_version; do
                [ -n "$want_plugin_id" ] || continue
                local matches=0
                while IFS=$'\t' read -r row_name row_plugin_id row_installed row_enabled row_version; do
                    [ -n "$row_name" ] || continue
                    [ "$row_name" = "$plugin_name" ] || continue
                    [ "$row_installed" = "true" ] || continue
                    if [ "$row_plugin_id" = "$want_plugin_id" ] &&
                        [ "$row_enabled" = "$want_enabled" ] &&
                        [ "$row_version" = "$want_version" ]; then
                        matches=$((matches + 1))
                    fi
                done <<< "$restored_rows"
                if [ "$matches" -ne 1 ]; then
                    rollback_succeeded=0
                fi
            done <<< "$duplicates"
        fi
    fi

    local restored_market_raw restored_market_rows
    if ! restored_market_raw="$(codex plugin marketplace list --json 2>/dev/null)" ||
        ! restored_market_rows="$(printf '%s' "$restored_market_raw" | json_query marketplaces 2>/dev/null)"; then
        rollback_succeeded=0
    else
        local restored_market_count=0 restored_market_root="" restored_market_source=""
        while IFS=$'\t' read -r row_name row_root row_source; do
            [ -n "$row_name" ] || continue
            if [ "$row_name" = "$marketplace" ]; then
                restored_market_count=$((restored_market_count + 1))
                restored_market_root="$row_root"
                restored_market_source="$row_source"
            fi
        done <<< "$restored_market_rows"
        if [ "$configured_count" -eq 1 ]; then
            if [ "$restored_market_count" -ne 1 ] || [ -z "${restored_market_root// /}" ]; then
                rollback_succeeded=0
            elif [ "$restored_from_remote" -eq 1 ]; then
                local actual_revision=""
                if ! actual_revision="$(git -C "$restored_market_root" rev-parse HEAD 2>/dev/null)"; then
                    rollback_succeeded=0
                else
                    actual_revision="$(printf '%s' "$actual_revision" | tr -d '[:space:]')"
                    if [ "$restored_market_source" != "$repository@$previous_release_commit" ] ||
                        [ "$actual_revision" != "$previous_release_commit" ]; then
                        rollback_succeeded=0
                    fi
                fi
            else
                if [ "$(canonical_path "$restored_market_root")" != "$(canonical_path "$rollback_marketplace")" ]; then
                    rollback_succeeded=0
                fi
            fi
        elif [ "$restored_market_count" -ne 0 ]; then
            rollback_succeeded=0
        fi
    fi

    # Kept whenever the rollback did not verify, not only when the profile was
    # pointed back at this copy. Reading "the remote took over" from the attempt
    # rather than from the verification meant a restore that came back on the
    # wrong revision, or left the plugin inventory short, deleted the recovery
    # copy while the failure message still told the operator to keep it -- so
    # the one case where recovery material matters most was the one that had
    # none, and the advice named a directory that was already gone.
    if [ "$rollback_prepared" -eq 1 ] &&
        { [ "$restored_from_remote" -eq 0 ] || [ "$rollback_succeeded" != "1" ]; }; then
        preserve_rollback=1
    fi
    return 0
}

if transact; then
    printf 'Cognitive Powers %s is installed and enabled from immutable ref %s. Restart Codex before starting a new task.\n' \
        "$expected_version" "$release_ref"
    exit 0
fi

rollback
if [ "$rollback_succeeded" = "1" ] && [ "$preserve_rollback" -eq 1 ]; then
    rollback_message="The previous installation was restored from recovery marketplace '$rollback_marketplace'; keep that directory until a remote immutable marketplace is re-established. Re-running this installer recognizes that recovery marketplace and resumes the upgrade from it."
elif [ "$rollback_succeeded" = "1" ]; then
    rollback_message="The previous installation was restored."
elif [ "$rollback_prepared" -eq 1 ]; then
    rollback_message="Automatic rollback was incomplete. Recovery marketplace preserved at '$rollback_marketplace'."
else
    rollback_message="No complete rollback copy was available."
fi
die "Installation of $release_ref failed. $rollback_message Original error: $transaction_error"
