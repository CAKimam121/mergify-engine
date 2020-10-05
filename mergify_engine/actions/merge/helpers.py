# -*- encoding: utf-8 -*-
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

import enum
import itertools

from mergify_engine import branch_updater
from mergify_engine import subscription
from mergify_engine.actions.merge import queue


class PriorityAliases(enum.Enum):
    low = 1000
    medium = 2000
    high = 3000


def merge_report(ctxt, strict):
    if ctxt.pull["draft"]:
        conclusion = None
        title = "Draft flag needs to be removed"
        summary = ""
    elif ctxt.pull["merged"]:
        if ctxt.pull["merged_by"]["login"] in [
            "mergify[bot]",
            "mergify-test[bot]",
        ]:
            mode = "automatically"
        else:
            mode = "manually"
        conclusion = "success"
        title = "The pull request has been merged %s" % mode
        summary = "The pull request has been merged %s at *%s*" % (
            mode,
            ctxt.pull["merge_commit_sha"],
        )
    elif ctxt.pull["state"] == "closed":
        conclusion = "cancelled"
        title = "The pull request has been closed manually"
        summary = ""

    # NOTE(sileht): Take care of all branch protection state
    elif ctxt.pull["mergeable_state"] == "dirty":
        conclusion = "cancelled"
        title = "Merge conflict needs to be solved"
        summary = ""

    elif ctxt.pull["mergeable_state"] == "unknown":
        conclusion = "failure"
        title = "Pull request state reported as `unknown` by GitHub"
        summary = ""
    # FIXME(sileht): We disable this check as github wrongly report
    # mergeable_state == blocked sometimes. The workaround is to try to merge
    # it and if that fail we checks for blocking state.
    # elif ctxt.pull["mergeable_state"] == "blocked":
    #     conclusion = "failure"
    #     title = "Branch protection settings are blocking automatic merging"
    #     summary = ""
    elif ctxt.pull["mergeable_state"] == "behind" and not strict:
        # Strict mode has been enabled in branch protection but not in
        # mergify
        conclusion = "failure"
        title = (
            "Branch protection setting 'strict' conflicts with Mergify configuration"
        )
        summary = ""

    elif ctxt.github_workflow_changed():
        conclusion = "action_required"
        title = "Pull request must be merged manually."
        summary = """GitHub App like Mergify are not allowed to merge pull request where `.github/workflows` is changed.
<br />
This pull request must be merged manually."""

    # NOTE(sileht): remaining state "behind, clean, unstable, has_hooks
    # are OK for us
    else:
        return

    return conclusion, title, summary


def get_queue_summary(ctxt):
    q = queue.Queue.from_context(ctxt)
    pulls = q.get_pulls()
    if not pulls:
        return ""

    # NOTE(sileht): It would be better to get that from configuration, but we
    # don't have it here, so just guess it.
    priorities_configured = False

    summary = "\n\nThe following pull requests are queued:"
    for priority, grouped_pulls in itertools.groupby(
        pulls, key=lambda v: q.get_config(v)["priority"]
    ):
        if priority != PriorityAliases.medium.value:
            priorities_configured = True

        try:
            fancy_priority = PriorityAliases(priority).name
        except ValueError:
            fancy_priority = priority
        formatted_pulls = ", ".join((f"#{p}" for p in grouped_pulls))
        summary += f"\n* {formatted_pulls} (priority: {fancy_priority})"

    if priorities_configured and not ctxt.subscription.has_feature(
        subscription.Features.PRIORITY_QUEUES
    ):
        summary += "\n\n⚠ *Ignoring merge priority*\n"
        summary += ctxt.subscription.missing_feature_reason(
            ctxt.pull["base"]["repo"]["owner"]["login"]
        )

    return summary


def get_strict_status(ctxt, rule=None, missing_conditions=None, need_update=False):
    if need_update:
        title = "Base branch will be updated soon"
        summary = "The pull request base branch will be updated soon and then merged."
    else:
        title = "Base branch update done"
        summary = "The pull request has been automatically updated to follow its base branch and will be merged soon."

    summary += get_queue_summary(ctxt)

    if not need_update and rule and missing_conditions is not None:
        summary += "\n\nRequired conditions for merge:\n"
        for cond in rule.conditions:
            checked = " " if cond in missing_conditions else "X"
            summary += f"\n- [{checked}] `{cond}`"

    return None, title, summary


def update_pull_base_branch(ctxt, method, user):
    try:
        if method == "merge":
            branch_updater.update_with_api(ctxt)
        else:
            branch_updater.update_with_git(ctxt, method, user)
    except branch_updater.BranchUpdateFailure as e:
        # NOTE(sileht): Maybe the PR have been rebased and/or merged manually
        # in the meantime. So double check that to not report a wrong status
        ctxt.update()
        output = merge_report(ctxt, True)
        if output:
            return output
        else:
            return ("failure", "Base branch update has failed", e.message)
    else:
        return get_strict_status(ctxt, need_update=False)
