const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {
  assertPullRequest, assertFiles, testsPassed, waitForTests, merge,
  projectVersion, nextVersion, versionFiles, release, MERGE_MARKER,
} = require('./dependabot.cjs');

const repo = { owner: 'example', repo: 'server' };
const pr = {
  number: 7, user: { login: 'dependabot[bot]' }, state: 'open', draft: false,
  head: { sha: 'tested-head', repo: { full_name: 'example/server' } },
  base: { ref: 'main' }, title: 'Bump dependencies',
};
const context = { repo, payload: { pull_request: pr } };
const checks = ['3.11', '3.12'].map((version, index) => ({
  id: index + 1, name: `Tests (${version})`, head_sha: pr.head.sha,
  app: { slug: 'github-actions' }, status: 'completed', conclusion: 'success',
}));

test('accepts only same-repository Dependabot PRs targeting main', () => {
  assert.doesNotThrow(() => assertPullRequest(pr, repo));
  for (const changed of [
    { ...pr, user: { login: 'someone' } },
    { ...pr, head: { ...pr.head, repo: null } },
    { ...pr, base: { ref: 'other' } },
    { ...pr, draft: true },
  ]) assert.throws(() => assertPullRequest(changed, repo));
});

test('accepts dependency files but rejects source changes, renames and deletions', () => {
  assertFiles(['pyproject.toml', 'uv.lock', '.github/workflows/release.yml']
    .map(filename => ({ filename, status: 'modified' })));
  for (const files of [
    [], [{ filename: 'src/server.py', status: 'modified' }],
    [{ filename: 'uv.lock', status: 'removed' }],
    [{ filename: 'pyproject.toml', status: 'renamed' }],
  ]) assert.throws(() => assertFiles(files));
});

test('requires successful checks from GitHub Actions on the exact tested head', () => {
  assert.equal(testsPassed(checks, pr.head.sha), true);
  assert.equal(testsPassed(checks, 'different-head'), false);
  assert.equal(testsPassed(checks.slice(0, 1), pr.head.sha), false);
  assert.equal(testsPassed(checks.map(check => ({ ...check, app: { slug: 'fake' } })),
    pr.head.sha), false);
  assert.throws(() => testsPassed([
    ...checks, { ...checks[0], id: 99, conclusion: 'failure' },
  ], pr.head.sha), /did not pass/);
  assert.equal(testsPassed([
    ...checks, { ...checks[0], id: 99, status: 'in_progress', conclusion: null },
  ], pr.head.sha), false);
});

test('waits for checks and refuses changed heads and timeouts', async () => {
  let calls = 0;
  const github = {
    rest: {
      pulls: { get: async () => ({ data: pr }) },
      checks: { listForRef() {} },
    },
    paginate: async () => ++calls === 1 ? [] : checks,
  };
  await waitForTests({ github, context }, { sleep: async () => {}, attempts: 2 });
  await assert.rejects(waitForTests({ github, context }, { attempts: 0 }), /Timed out/);
  github.rest.pulls.get = async () => ({ data: { ...pr, head: { ...pr.head, sha: 'new' } } });
  await assert.rejects(waitForTests({ github, context }), /changed/);
});

test('merges with a head SHA guard and an explicit release marker', async () => {
  let request;
  const github = {
    rest: { pulls: {
      get: async () => ({ data: pr }),
      listFiles() {},
      merge: async input => {
        request = input;
        return { data: { merged: true } };
      },
    } },
    paginate: async () => [{ filename: 'uv.lock', status: 'modified' }],
  };
  await merge({ github, context });
  assert.equal(request.sha, pr.head.sha);
  assert.equal(request.merge_method, 'squash');
  assert.equal(request.commit_message, MERGE_MARKER);
  github.rest.pulls.merge = async () => ({ data: { merged: false, message: 'Blocked' } });
  await assert.rejects(merge({ github, context }), /Blocked/);
});

test('increments the highest stable tag or source version numerically', () => {
  assert.equal(nextVersion('0.1.3', []), '0.1.4');
  assert.equal(nextVersion('0.1.3', ['v0.1.9', 'v0.1.10', 'v9.0.0rc1', 'other']), '0.1.11');
  assert.equal(nextVersion('2.0.0', ['v0.9.99']), '2.0.1');
  assert.equal(nextVersion('0.1.3', ['v1.0.0', 'v0.99.99']), '1.0.1');
});

test('updates exactly the project version and its lock entry in repository inputs', () => {
  const project = fs.readFileSync('pyproject.toml', 'utf8');
  const lock = fs.readFileSync('uv.lock', 'utf8');
  const current = projectVersion(project);
  const updated = versionFiles(project, lock, '0.1.999');
  assert.equal(projectVersion(updated['pyproject.toml']), '0.1.999');
  assert.equal(updated['pyproject.toml'].replace('version = "0.1.999"', `version = "${current}"`),
    project);
  assert.equal(updated['uv.lock'].replace(
    'name = "bitbucket-mcp-server-datacenter"\nversion = "0.1.999"',
    `name = "bitbucket-mcp-server-datacenter"\nversion = "${current}"`), lock);
  assert.throws(() => projectVersion('[project]\nversion = "dynamic"\n'), /static/);
  assert.throws(() => versionFiles(project, '', '0.1.999'), /inconsistent/);
});

function releaseFixture() {
  const releasePr = { ...pr, merged: true, merge_commit_sha: 'merged-source' };
  const state = { tags: [], commits: new Map(), created: [], collisions: 0 };
  const github = {
    paginate: async () => state.tags,
    rest: {
      repos: {
        listTags() {},
        getContent: async ({ path }) => ({
          data: { type: 'file', encoding: 'base64',
            content: fs.readFileSync(path).toString('base64') },
        }),
      },
      git: {
        getCommit: async ({ commit_sha }) => ({
          data: commit_sha === 'merged-source'
            ? { message: `Update dependencies\n\n${MERGE_MARKER}`, tree: { sha: 'source-tree' } }
            : state.commits.get(commit_sha),
        }),
        createTree: async input => {
          state.tree = input;
          return { data: { sha: 'version-tree' } };
        },
        createCommit: async input => {
          state.commit = input;
          const sha = `release-${state.commits.size}`;
          state.commits.set(sha, input);
          return { data: { sha } };
        },
        createRef: async input => {
          if (state.collisions > 0) {
            state.collisions--;
            state.tags.push({ name: input.ref.replace('refs/tags/', ''), commit: { sha: 'other' } });
            state.commits.set('other', { message: 'Concurrent release' });
            throw Object.assign(new Error('Already exists'), { status: 422 });
          }
          state.created.push(input);
          state.tags.push({ name: input.ref.replace('refs/tags/', ''), commit: { sha: input.sha } });
        },
        getRef: async () => ({ data: {} }),
      },
    },
  };
  return { github, context: { repo, payload: { pull_request: releasePr } }, state };
}

test('tags a version-only commit parented by the exact merged source, without pushing main', async () => {
  const fixture = releaseFixture();
  await release(fixture);
  const { state } = fixture;
  assert.deepEqual(state.commit.parents, ['merged-source']);
  assert.equal(state.tree.base_tree, 'source-tree');
  assert.deepEqual(state.tree.tree.map(file => file.path), ['pyproject.toml', 'uv.lock']);
  assert.equal(state.created.length, 1);
  assert.match(state.created[0].ref, /^refs\/tags\/v\d+\.\d+\.\d+$/);
  await release(fixture);
  assert.equal(state.created.length, 1, 'rerunning must not publish another version');
});

test('retries a concurrent tag collision with a higher version', async () => {
  const fixture = releaseFixture();
  fixture.state.collisions = 1;
  await release(fixture);
  assert.equal(fixture.state.tags.length, 2);
  assert.equal(fixture.state.created.length, 1);
  assert.notEqual(fixture.state.tags[0].name, fixture.state.tags[1].name);
});

test('does not automatically release manually merged PRs or hide API failures', async () => {
  const fixture = releaseFixture();
  fixture.github.rest.git.getCommit = async () => ({
    data: { message: 'Manually merged', tree: { sha: 'source-tree' } },
  });
  await release(fixture);
  assert.equal(fixture.state.created.length, 0);
  fixture.github.rest.git.getCommit = async () => { throw new Error('API unavailable'); };
  await assert.rejects(release(fixture), /API unavailable/);
});

test('fails explicitly on unreadable inputs, denied tag creation and repeated collisions', async () => {
  const unreadable = releaseFixture();
  unreadable.github.rest.repos.getContent = async () => ({
    data: { type: 'dir', encoding: 'base64' },
  });
  await assert.rejects(release(unreadable), /Cannot read release input/);

  const denied = releaseFixture();
  denied.github.rest.git.createRef = async () => {
    throw Object.assign(new Error('Permission denied'), { status: 403 });
  };
  await assert.rejects(release(denied), /Permission denied/);

  const concurrent = releaseFixture();
  concurrent.state.collisions = 5;
  await assert.rejects(release(concurrent), /exhausted/);
});
