const assert = require("node:assert/strict");
const test = require("node:test");

const { pageEffect } = require("../jev_ultrafast/static/app.js");

test("action history distinguishes observed and unknown outcomes", () => {
  assert.equal(pageEffect(true), "Page changed");
  assert.equal(pageEffect(false), "No change observed");
  assert.equal(pageEffect(null), "Outcome not observed");
  assert.equal(pageEffect(undefined), "Outcome not observed");
});
