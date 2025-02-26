#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
import typing

import daiquiri
import pkg_resources
import voluptuous
import yaml

from mergify_engine import check_api
from mergify_engine import config
from mergify_engine import context
from mergify_engine import github_types
from mergify_engine import rules
from mergify_engine import subscription
from mergify_engine import utils
from mergify_engine.clients import github
from mergify_engine.engine import actions_runner
from mergify_engine.engine import commands_runner
from mergify_engine.engine import queue_runner


LOG = daiquiri.getLogger(__name__)

mergify_rule_path = pkg_resources.resource_filename(
    "mergify_engine", "data/default_pull_request_rules.yml"
)

with open(mergify_rule_path, "r") as f:
    DEFAULT_PULL_REQUEST_RULES = voluptuous.Schema(rules.PullRequestRulesSchema)(
        yaml.safe_load(f.read())["rules"]
    )


async def _check_configuration_changes(
    ctxt: context.Context,
    current_mergify_config_file: typing.Optional[context.MergifyConfigFile],
) -> bool:
    if ctxt.pull["base"]["repo"]["default_branch"] != ctxt.pull["base"]["ref"]:
        return False

    config_file_to_validate: typing.Optional[context.MergifyConfigFile] = None
    preferred_filename = (
        None
        if current_mergify_config_file is None
        else current_mergify_config_file["path"]
    )
    # NOTE(sileht): Just a shorcut to do two requests instead of three.
    if ctxt.pull["changed_files"] <= 100:
        for f in await ctxt.files:
            if f["filename"] in context.Repository.MERGIFY_CONFIG_FILENAMES:
                preferred_filename = f["filename"]
                break
        else:
            return False

    async for config_file in ctxt.repository.iter_mergify_config_files(
        ref=ctxt.pull["head"]["sha"], preferred_filename=preferred_filename
    ):
        if (
            current_mergify_config_file is None
            or config_file["path"] != current_mergify_config_file["path"]
        ):
            config_file_to_validate = config_file
            break
        elif config_file["sha"] != current_mergify_config_file["sha"]:
            config_file_to_validate = config_file
            break

    if config_file_to_validate is None:
        return False

    try:
        rules.get_mergify_config(config_file_to_validate)
    except rules.InvalidRules as e:
        # Not configured, post status check with the error message
        await ctxt.set_summary_check(
            check_api.Result(
                check_api.Conclusion.FAILURE,
                title="The new Mergify configuration is invalid",
                summary=str(e),
                annotations=e.get_annotations(e.filename),
            )
        )
    else:
        await ctxt.set_summary_check(
            check_api.Result(
                check_api.Conclusion.SUCCESS,
                title="The new Mergify configuration is valid",
                summary="This pull request must be merged manually because it modifies Mergify configuration",
            )
        )
    return True


async def _get_summary_from_sha(ctxt, sha):
    checks = await check_api.get_checks_for_ref(
        ctxt,
        sha,
        check_name=ctxt.SUMMARY_NAME,
    )
    checks = [c for c in checks if c["app"]["id"] == config.INTEGRATION_ID]
    if checks:
        return checks[0]


async def _ensure_summary_on_head_sha(ctxt: context.Context) -> None:
    for check in await ctxt.pull_engine_check_runs:
        if check["name"] == ctxt.SUMMARY_NAME and actions_runner.load_conclusions_line(
            ctxt, check
        ):
            return

    opened = ctxt.has_been_opened()
    sha = await ctxt.get_cached_last_summary_head_sha()
    if sha is None:
        if not opened:
            ctxt.log.warning(
                "the pull request doesn't have the last summary head sha stored in redis"
            )
        return

    previous_summary = await _get_summary_from_sha(ctxt, sha)
    if previous_summary:
        await ctxt.set_summary_check(
            check_api.Result(
                check_api.Conclusion(previous_summary["conclusion"]),
                title=previous_summary["output"]["title"],
                summary=previous_summary["output"]["summary"],
            )
        )
    elif not opened:
        ctxt.log.warning("the pull request doesn't have a summary")


class T_PayloadEventIssueCommentSource(typing.TypedDict):
    event_type: github_types.GitHubEventType
    data: github_types.GitHubEventIssueComment
    timestamp: str


async def run(
    ctxt: context.Context,
    sources: typing.List[context.T_PayloadEventSource],
) -> None:
    LOG.debug("engine get context")
    ctxt.log.debug("engine start processing context")

    issue_comment_sources: typing.List[T_PayloadEventIssueCommentSource] = []

    for source in sources:
        if source["event_type"] == "issue_comment":
            issue_comment_sources.append(
                typing.cast(T_PayloadEventIssueCommentSource, source)
            )
        else:
            ctxt.sources.append(source)

    ctxt.log.debug("engine run pending commands")
    await commands_runner.run_pending_commands_tasks(ctxt)

    if issue_comment_sources:
        ctxt.log.debug("engine handle commands")
        for ic_source in issue_comment_sources:
            await commands_runner.handle(
                ctxt,
                ic_source["data"]["comment"]["body"],
                ic_source["data"]["comment"]["user"],
            )

    if not ctxt.sources:
        return

    if ctxt.client.auth.permissions_need_to_be_updated:
        await ctxt.set_summary_check(
            check_api.Result(
                check_api.Conclusion.FAILURE,
                title="Required GitHub permissions are missing.",
                summary="You can accept them at https://dashboard.mergify.io/",
            )
        )
        return

    config_file = await ctxt.repository.get_mergify_config_file()

    ctxt.log.debug("engine check configuration change")
    if await _check_configuration_changes(ctxt, config_file):
        ctxt.log.info("Configuration changed, ignoring")
        return

    ctxt.log.debug("engine get configuration")
    if config_file is None:
        ctxt.log.info("No need to proceed queue (.mergify.yml is missing)")
        return

    # BRANCH CONFIGURATION CHECKING
    try:
        mergify_config = rules.get_mergify_config(config_file)
    except rules.InvalidRules as e:  # pragma: no cover
        ctxt.log.info(
            "The Mergify configuration is invalid",
            summary=str(e),
            annotations=e.get_annotations(e.filename),
        )
        # Not configured, post status check with the error message
        for s in ctxt.sources:
            if s["event_type"] == "pull_request":
                event = typing.cast(github_types.GitHubEventPullRequest, s["data"])
                if event["action"] in ("opened", "synchronize"):
                    await ctxt.set_summary_check(
                        check_api.Result(
                            check_api.Conclusion.FAILURE,
                            title="The Mergify configuration is invalid",
                            summary=str(e),
                            annotations=e.get_annotations(e.filename),
                        )
                    )
                    break
        return

    # Add global and mandatory rules
    mergify_config["pull_request_rules"].rules.extend(DEFAULT_PULL_REQUEST_RULES.rules)

    if ctxt.pull["base"]["repo"]["private"] and not ctxt.subscription.has_feature(
        subscription.Features.PRIVATE_REPOSITORY
    ):
        ctxt.log.info("mergify disabled: private repository")
        await ctxt.set_summary_check(
            check_api.Result(
                check_api.Conclusion.FAILURE,
                title="Mergify is disabled",
                summary=ctxt.subscription.reason,
            )
        )
        return

    await _ensure_summary_on_head_sha(ctxt)

    # NOTE(jd): that's fine for now, but I wonder if we wouldn't need a higher abstraction
    # to have such things run properly. Like hooks based on events that you could
    # register. It feels hackish otherwise.
    for s in ctxt.sources:
        if s["event_type"] == "pull_request":
            event = typing.cast(github_types.GitHubEventPullRequest, s["data"])
            if event["action"] == "closed":
                await ctxt.clear_cached_last_summary_head_sha()
                break

    ctxt.log.debug("engine handle actions")
    if ctxt.is_merge_queue_pr():
        await queue_runner.handle(mergify_config["queue_rules"], ctxt)
    else:
        await actions_runner.handle(mergify_config["pull_request_rules"], ctxt)


async def create_initial_summary(
    redis: utils.RedisCache, event: github_types.GitHubEventPullRequest
) -> None:
    owner = event["repository"]["owner"]["login"]

    if not await redis.exists(
        context.Repository.get_config_location_cache_key(
            event["pull_request"]["base"]["repo"]["owner"]["login"],
            event["pull_request"]["base"]["repo"]["name"],
        )
    ):
        # Mergify is probably not activated on this repo
        return

    # NOTE(sileht): It's possible that a "push" event creates a summary before we
    # received the pull_request/opened event.
    # So we check first if a summary does not already exists, to not post
    # the summary twice. Since this method can ran in parallel of the worker
    # this is not a 100% reliable solution, but if we post a duplicate summary
    # check_api.set_check_run() handle this case and update both to not confuse users.
    sha = await context.Context.get_cached_last_summary_head_sha_from_pull(
        redis, event["pull_request"]
    )

    if sha is not None or sha == event["pull_request"]["head"]["sha"]:
        return

    async with github.aget_client(owner) as client:
        post_parameters = {
            "name": context.Context.SUMMARY_NAME,
            "head_sha": event["pull_request"]["head"]["sha"],
            "status": check_api.Status.IN_PROGRESS.value,
            "started_at": utils.utcnow().isoformat(),
            "details_url": f"{event['pull_request']['html_url']}/checks",
            "output": {
                "title": "Your rules are under evaluation",
                "summary": "Be patient, the page will be updated soon.",
            },
        }
        await client.post(
            f"/repos/{event['pull_request']['base']['user']['login']}/{event['pull_request']['base']['repo']['name']}/check-runs",
            api_version="antiope",  # type: ignore[call-arg]
            json=post_parameters,
        )
