.. meta::
   :description: Mergify Documentation for Merge Action
   :keywords: mergify, merge, pull request
   :summary: Merge a pull request.
   :doc:icon: code-branch

.. _merge action:

=======
 merge
=======

The ``merge`` action merges the pull request into its base branch. The
``merge`` action takes the following parameter:

.. list-table::
   :header-rows: 1
   :widths: 1 1 1 3

   * - Key Name
     - Value Type
     - Default
     - Value Description
   * - ``method``
     - string
     - ``merge``
     - Merge method to use. Possible values are ``merge``, ``squash`` or
       ``rebase``.
   * - ``rebase_fallback``
     - string
     - ``merge``
     - If ``method`` is set to ``rebase``, but the pull request cannot be
       rebased, the method defined in ``rebase_fallback`` will be used instead.
       Possible values are ``merge``, ``squash``, ``null``.
   * - ``strict``
     - Boolean, ``smart`` or ``smart+fasttrack``
     - ``false``
     - Determines whether to use :ref:`strict merge`:

       * ``true`` enables :ref:`strict merge`. The pull request will be merged
         only once up-to-date with its base branch. When multiple pull requests
         are ready to be merged, they will **all** be updated with their base
         branch at the same time, and the first ready to be merged will be
         merged; the remaining pull request will be updated once again.

       * ``smart`` enables :ref:`strict merge` but only update one pull request
         against its base branch at a time.
         This allows you to e.g., save CI time, as Mergify will queue the
         mergeable pull requests and update them serially, one at a time.

       * ``smart+fasttrack`` enables :ref:`strict merge` with the same behavior as ``smart``,
         except if the pull request is already in sync with its base branch,
         the queue is bypassed and the pull request is directly merged.

       * ``false`` disables :ref:`strict merge` and merge pull requests as soon
         as possible, without bringing the pull request up-to-date with its
         base branch.


   * - ``strict_method``
     - string
     - ``merge``
     - Method to use to update the pull request with its base branch
       when :ref:`strict merge` is enabled. Possible values:

       * ``merge`` to merge the base branch into the pull request.
       * ``rebase`` to rebase the pull request against its base branch.

       Note that the ``rebase`` method has some drawbacks, see `<Strict rebase>`_.

   * - ``merge_bot_account``
     - :ref:`data type template`
     -
     - Mergify can impersonate a GitHub user to merge pull request.
       If no ``merge_bot_account`` is set, Mergify will merge the pull request
       itself. The user account **must** have already been
       logged in Mergify dashboard once and have **write** or **maintain** permission.

       |premium plan tag|

   * - ``update_bot_account``
     - :ref:`data type template`
     -
     - For certain actions, such as rebasing branches, Mergify has to
       impersonate a GitHub user. You can specify the account to use with this
       option. If no ``update_bot_account`` is set, Mergify picks randomly one of the
       organization users instead. The user account **must** have already been
       logged in Mergify dashboard once.

       |premium plan tag|

   * - ``priority``
     - 1 <= integer <= 10000 or ``low`` or ``medium`` or ``high``
     - ``medium``
     - This sets the priority of the pull request in the queue when ``smart``
       :ref:`strict merge` is enabled. The pull request with the highest priority is merged first.
       ``low``, ``medium``, ``high`` are aliases for ``1000``, ``2000``, ``3000``.

       |premium plan tag|

   * - ``commit_message``
     - string
     - ``default``
     - Defines what commit message to use when merging using the ``squash`` or
       ``merge`` method. Possible values are:

       * ``default`` to use the default commit message provided by GitHub
         or defined in the pull request body (see :ref:`commit message`).

       * ``title+body`` means to use the title and body from the pull request
         itself as the commit message. The pull request number will be added to
         end of the title.

Branch Protection Settings
--------------------------

Note that Mergify will always respect the branch protection settings. When the
conditions match and the ``merge`` action runs, Mergify waits for the branch
protection to be validated before merging the pull request.

.. _commit message:

Defining the Commit Message
---------------------------

When a pull request is merged using the ``squash`` or ``merge`` method, you can
override the default commit message. To that end, you need to add a section in
the pull request body that starts with ``Commit Message``.

.. code-block:: md

    ## Commit Message

    My wanted commit title

    The whole commit message finishes at the end of the pull request body or
    before a new Markdown title.

The whole commit message finishes at the end of the pull request body or before
a new Markdown title.

You can use any available attributes of the pull request in the commit message,
by writing using the :ref:`templating <data type template>` language:

For example:

.. code-block:: jinja

    ## Commit Message

    {{title}}

    This pull request implements magnificient features, and I would like to
    talk about them. This has been written by {{author}} and has been reviewed
    by:

    {% for user in approved_reviews_by %}
    - {{user}}
    {% endfor %}

Check the :ref:`data type template` for more details on the format.

.. note::

   This feature only works when ``commit_message`` is set to ``default``.

.. _strict merge:

Strict Merge
------------

The `strict merge` option enables a workflow that prevents merging broken
pull requests. That situation can arise when outdated pull requests are being
merged in their base branch.

Understanding the Problem
=========================

To understand the problem, imagine the following situation:

- The base branch (e.g., ``main``) has its continuous integration testing
  passing correctly.

- A pull request is created, which also passes the CI.

The state of the repository can be represented like this:

.. graphviz:: strict-mode-master-pr-ci-pass.dot
   :alt: Pull request is open

While the pull request is open, another commit is pushed to ``main`` — let's
call it `new commit`. That can be a local commit or a merge commit from another
pull request; who knows. The tests are run against ``main`` by the CI and
they pass. The state of the repository and its continuous integration system
can be described like this:

.. graphviz:: strict-mode-new-master-pr-ci-pass.dot
   :alt: Base branch adds a new commit

The pull request is still marked as valid by the continuous integration system
since it did not change. As there is no code conflicts, the pull request is
considered as `mergeable` by GitHub: the merge button is green.

If you click that merge button, this is what `might` happen:

.. graphviz:: strict-mode-merge-ci-fail.dot
   :alt: Base branch is broken

As a new merge commit is created to merge the pull request, it is possible that
the continuous integration testing fails. Indeed, the continuous integration
did not test the pull request with the `new commit` that has been added to the
base branch. Some new test might have been introduced by this `new commit` in
the base branch while the pull request was open. That pull request may not have
the correct code to pass this new test.

Now you realize that by pressing the merge button, you just broke your
continuous integration system, and probably your software. Good job! 🤕


The Strict Merge Solution
=========================

The `strict merge` option solves that issue by updating any pull request that
is not up-to-date with its base branch before being merged. That forces the
continuous integration system to test again the pull request with the new code.

In the previous example, if the ``strict`` option was enabled, Mergify would
have merged ``main`` in the base branch automatically. The continuous
integration system would have run again and marked the pull request as failing
the test, removing it from the mergeable candidate.

.. graphviz:: strict-mode-rebase-ci-fail.dot
   :alt: Rebase make CI fails

When the ``strict`` option is enabled, Mergify takes care of merging the target
branch in any pull request that is not up-to-date with its target branch. If
multiple pull requests are mergeable, they are scheduled to be merged
sequentially, and they will be updated on top of each other.

The pull request branch update is only done when the pull request is ready to
be merged by the engine, e.g., when all the `conditions` are validated.


Enabling the Strict Option
==========================

To enable `strict merge`, you need to set the value of ``strict`` to ``true``
in the :ref:`merge action` action. For example:

.. code-block:: yaml

    pull_request_rules:
      - name: automatic merge with strict
        conditions:
          - "#approved-reviews-by>=2"
          - check-success=continuous-integration/travis-ci/pr
        actions:
          merge:
            method: merge
            strict: true


Advanced Queueing Option
========================

While `strict merge` solves most issues, the `queue action <queue action>`_ and
:ref:`queue rules` provide a more powerful action to have total control of the
merge queue.


Viewing the Merge Queue
=======================

When several pull request are ready to be merged and the `strict` option is
enabled, Mergify needs to merge serially. To do that, it handles a `merge
queue`. This queue can be seen from your `dashboard
<https://dashboard.mergify.io>`_:

.. figure:: ../_static/merge-queue.png
   :alt: The strict merge queue


.. _strict rebase:

Strict Rebase
=============

Using the ``rebase`` method for the strict merge has many drawbacks:

* It doesn't work for private forked repositories.

* It doesn't work if Mergify is used as a GitHub Action.

* Due to the change of all commits SHA-1 of the pull request, your
  contributor will need to force-push its own branch if they add new
  commits.

* GitHub branch protection of your repository may dismiss approved reviews.

* GitHub branch protection of the contributor repository may refuse Mergify to
  force push the rebased pull request.

* GPG signed commits will lost their signatures.

* Mergify will use a token from one of the repository member to force-push the
  branch. GitHub Applications are not allowed to do that so. This is a GitHub
  limitation that we already have reported — there is not much Mergify can do
  about that. In order to make this works, Mergify randomly picks and borrows a
  token from one of your repository member and uses it to force-push the
  rebased branch. The GitHub UI will show your collaborator as the author of
  the push, while it actually has been executed by Mergify.


Git merge workflow and Mergify equivalent configuration
-------------------------------------------------------

There is a lot of different ways to merge pull requests. You can replicate many
``git`` based merge when using Mergify — here's a list of equivalence.

.. note::

   `base branch` is usually ``master``, ``main`` or ``dev``,
   `head branch` is the pull request branch.


.. list-table::
   :header-rows: 1
   :widths: 2 2

   * - Git merge workflow
     - Mergify configuration

   * - ::

         (on head branch) $ git merge --no-ff base

     - ::

         merge:
           method: merge

   * - ::

         (on head branch) $ git merge --no-ff base
         (on head branch) # Wait for CI to go green
         (on base branch) $ git merge --no-ff head

     - ::

         merge:
           strict: true
           method: merge

   * - ::

         (on head branch) $ git rebase base
         (on base branch) $ git merge --ff head

     - ::

         merge:
           method: rebase

   * - ::

         (on head branch) $ git merge --no-ff base
         (on head branch) # Wait for CI to go green
         (on head branch) $ git rebase base
         (on base branch) $ git merge --ff head

     - ::

         merge:
           strict: true
           method: rebase

   * - ::

         (on head branch) $ git rebase base
         (on head branch) # Wait for CI to go green
         (on base branch) $ git merge --no-ff head

     - ::

         merge:
           strict: true
           strict_method: rebase
           method: merge

   * - ::

        (on head branch) # Squash all commits
        (on base branch) $ git merge --ff head

     - ::

         merge:
           method: squash

   * - ::

         (on head branch) $ git merge --no-ff base
         (on head branch) # Wait for CI to go green
         (on head branch) # Squash all commits
         (on base branch) $ git merge --ff head

     - ::

         merge:
           strict: true
           method: squash

   * - ::

         (on head branch) $ git rebase base
         (on head branch) # Wait for CI to go green
         (on head branch) # Squash all commits
         (on base branch) $ git merge --ff head

     - ::

         merge:
           strict: true
           strict_method: rebase
           method: squash

   * - ::

         (on head branch) $ git rebase base
         (on head branch) # Squash all commits
         (on head branch) # Mergify wait for CI
         (on head branch) $ git merge --no-ff head

     - ::

         merge:
           strict: true
           strict_method: squash
           method: merge

       `(not yet implemented)`

.. include:: ../global-substitutions.rst
