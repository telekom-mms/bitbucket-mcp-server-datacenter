const TESTS = ['Tests (3.11)', 'Tests (3.12)'];
const PACKAGE = 'bitbucket-mcp-server-datacenter';
const MERGE_MARKER = 'Automatically merged tested Dependabot update.';

function assertPullRequest(pr, repo) {
  if (pr.user.login !== 'dependabot[bot]' ||
      pr.head.repo?.full_name !== `${repo.owner}/${repo.repo}` ||
      pr.base.ref !== 'main' || pr.draft) {
    throw new Error('Expected a same-repository, non-draft Dependabot PR targeting main');
  }
}

function assertFiles(files) {
  if (!files.length || files.some(file =>
    !['modified', 'added'].includes(file.status) ||
    !/^(pyproject\.toml|uv\.lock|\.github\/workflows\/[^/]+\.ya?ml)$/.test(file.filename))) {
    throw new Error('Dependabot PR contains unexpected files or deletions');
  }
}

function testsPassed(checks, sha) {
  return TESTS.every(name => {
    const matching = checks.filter(check =>
      check.name === name && check.head_sha === sha && check.app?.slug === 'github-actions');
    const latest = matching.sort((a, b) => b.id - a.id)[0];
    if (latest?.status === 'completed' && latest.conclusion !== 'success') {
      throw new Error(`${name} did not pass: ${latest.conclusion}`);
    }
    return latest?.status === 'completed' && latest.conclusion === 'success';
  });
}

async function waitForTests({ github, context }, {
  sleep = ms => new Promise(resolve => setTimeout(resolve, ms)),
  attempts = 120,
} = {}) {
  const repo = context.repo;
  const original = context.payload.pull_request;
  for (let attempt = 0; attempt < attempts; attempt++) {
    const { data: pr } = await github.rest.pulls.get({ ...repo, pull_number: original.number });
    assertPullRequest(pr, repo);
    if (pr.state !== 'open' || pr.head.sha !== original.head.sha) {
      throw new Error('PR was closed or changed; refusing to merge an untested revision');
    }
    const checks = await github.paginate(github.rest.checks.listForRef, {
      ...repo, ref: pr.head.sha, per_page: 100,
    });
    if (testsPassed(checks, pr.head.sha)) return;
    await sleep(10000);
  }
  throw new Error('Timed out waiting for required Python tests; rerun this workflow after fixing CI');
}

async function merge({ github, context }) {
  const repo = context.repo;
  const original = context.payload.pull_request;
  const { data: pr } = await github.rest.pulls.get({ ...repo, pull_number: original.number });
  assertPullRequest(pr, repo);
  if (pr.state !== 'open' || pr.head.sha !== original.head.sha) {
    throw new Error('PR was closed or changed before merge');
  }
  const files = await github.paginate(github.rest.pulls.listFiles, {
    ...repo, pull_number: pr.number, per_page: 100,
  });
  assertFiles(files);
  const { data: result } = await github.rest.pulls.merge({
    ...repo, pull_number: pr.number, sha: pr.head.sha, merge_method: 'squash',
    commit_title: `${pr.title} (#${pr.number})`,
    commit_message: MERGE_MARKER,
  });
  if (!result.merged) throw new Error(`Merge refused: ${result.message}`);
}

function projectVersion(project) {
  const section = project.match(/^\[project\]\r?\n([\s\S]*?)(?=^\[|(?![\s\S]))/m);
  const version = section?.[1].match(/^version = "(\d+\.\d+\.\d+)"$/m)?.[1];
  if (!version) throw new Error('Expected a static major.minor.patch project version');
  return version;
}

function nextVersion(current, tags) {
  const versions = [current, ...tags.filter(tag => /^v\d+\.\d+\.\d+$/.test(tag))
    .map(tag => tag.slice(1))].map(version => version.split('.').map(Number));
  versions.sort((a, b) => b[0] - a[0] || b[1] - a[1] || b[2] - a[2]);
  const [major, minor, patch] = versions[0];
  return `${major}.${minor}.${patch + 1}`;
}

function versionFiles(project, lock, version) {
  const current = projectVersion(project);
  const updatedProject = project.replace(
    /^(\[project\]\r?\n[\s\S]*?^version = ")\d+\.\d+\.\d+(")$/m,
    (_, before, after) => `${before}${version}${after}`,
  );
  const packagePattern = new RegExp(
    `(\\[\\[package\\]\\]\\r?\\nname = "${PACKAGE}"\\r?\\nversion = ")${current.replaceAll('.', '\\.')}(")`,
  );
  if (!packagePattern.test(lock)) throw new Error('Project version and uv.lock are inconsistent');
  return {
    'pyproject.toml': updatedProject,
    'uv.lock': lock.replace(packagePattern, (_, before, after) => `${before}${version}${after}`),
  };
}

async function release({ github, context }) {
  const repo = context.repo;
  const pr = context.payload.pull_request;
  assertPullRequest(pr, repo);
  if (!pr.merged || !pr.merge_commit_sha) throw new Error('Expected a merged PR');
  const { data: source } = await github.rest.git.getCommit({
    ...repo, commit_sha: pr.merge_commit_sha,
  });
  if (!source.message.split('\n').includes(MERGE_MARKER)) {
    console.log('Not merged by the automation; no automatic release will be created');
    return;
  }
  const files = {};
  for (const path of ['pyproject.toml', 'uv.lock']) {
    const { data } = await github.rest.repos.getContent({
      ...repo, path, ref: pr.merge_commit_sha,
    });
    if (data.type !== 'file' || data.encoding !== 'base64') {
      throw new Error(`Cannot read release input: ${path}`);
    }
    files[path] = Buffer.from(data.content, 'base64').toString('utf8');
  }
  const marker = `Dependabot release for #${pr.number} (${pr.merge_commit_sha})`;
  for (let attempt = 0; attempt < 5; attempt++) {
    const tags = await github.paginate(github.rest.repos.listTags, { ...repo, per_page: 100 });
    for (const tag of tags.filter(tag => /^v\d+\.\d+\.\d+$/.test(tag.name))) {
      const { data: commit } = await github.rest.git.getCommit({
        ...repo, commit_sha: tag.commit.sha,
      });
      if (commit.message.split('\n').includes(marker)) {
        console.log(`Release already tagged: ${tag.name}`);
        return;
      }
    }
    const version = nextVersion(projectVersion(files['pyproject.toml']), tags.map(tag => tag.name));
    const updated = versionFiles(files['pyproject.toml'], files['uv.lock'], version);
    const { data: tree } = await github.rest.git.createTree({
      ...repo, base_tree: source.tree.sha,
      tree: Object.entries(updated).map(([path, content]) => ({
        path, mode: '100644', type: 'blob', content,
      })),
    });
    const { data: commit } = await github.rest.git.createCommit({
      ...repo, tree: tree.sha, parents: [pr.merge_commit_sha],
      message: `Release v${version}\n\n${marker}\n\nCo-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`,
    });
    try {
      await github.rest.git.createRef({
        ...repo, ref: `refs/tags/v${version}`, sha: commit.sha,
      });
      console.log(`Created v${version}; the tag push triggers the PyPI release workflow`);
      return;
    } catch (error) {
      if (error.status !== 422) throw error;
      // Another release can claim the same version between listing and creating tags.
      await github.rest.git.getRef({ ...repo, ref: `tags/v${version}` });
    }
  }
  throw new Error('Concurrent releases exhausted tag creation retries; rerun this job');
}

module.exports = {
  assertPullRequest, assertFiles, testsPassed, waitForTests, merge,
  projectVersion, nextVersion, versionFiles, release, MERGE_MARKER,
};
