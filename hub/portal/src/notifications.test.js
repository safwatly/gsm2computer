import assert from "node:assert/strict";
import test from "node:test";
import { notificationCopy, notificationDelivery, osNotificationsAvailable, shouldNotifySms } from "./notifications.js";

test("os notifications require a secure context", () => {
  assert.equal(osNotificationsAvailable(false, true), false);
  assert.equal(osNotificationsAvailable(true, true), true);
  assert.equal(osNotificationsAvailable(true, false), false);
});

test("insecure context always uses in-app delivery", () => {
  assert.equal(notificationDelivery({ isSecure: false, permission: "granted" }), "in-app");
  assert.equal(notificationDelivery({ isSecure: true, permission: "granted" }), "os");
  assert.equal(notificationDelivery({ isSecure: true, permission: "default" }), "in-app");
});

test("do not toast the thread you are already reading", () => {
  const event = { type: "message", direction: "in", peer: "+1", body: "hi" };
  assert.equal(
    shouldNotifySms(event, { selectedPeer: "+1", documentHidden: false, route: "messaging" }),
    false,
  );
  assert.equal(
    shouldNotifySms(event, { selectedPeer: "+1", documentHidden: true, route: "messaging" }),
    true,
  );
  assert.equal(
    shouldNotifySms(event, { selectedPeer: "+2", documentHidden: false, route: "messaging" }),
    true,
  );
});

test("queued outbound echo is not a desk alert", () => {
  assert.equal(
    shouldNotifySms(
      { type: "message", direction: "out", status: "queued", peer: "+1" },
      { selectedPeer: "+2", documentHidden: false, route: "messaging" },
    ),
    false,
  );
  assert.equal(
    shouldNotifySms(
      { type: "message", direction: "out", status: "sent", peer: "+1" },
      { selectedPeer: "+2", documentHidden: false, route: "messaging" },
    ),
    true,
  );
});

test("a failed send is not announced as delivered", () => {
  const copy = notificationCopy({
    direction: "out",
    peer: "+20100",
    status: "failed",
    error: "result=40 MODEM_ERR rp=38",
    body: "hello",
  });
  assert.equal(copy.title, "SMS failed to +20100");
  assert.equal(copy.body, "result=40 MODEM_ERR rp=38");
  const timedOut = notificationCopy({
    direction: "out",
    peer: "+20100",
    status: "timeout",
    body: "hello",
  });
  assert.equal(timedOut.title, "SMS timed out to +20100");
});

test("inbound copy", () => {
  const copy = notificationCopy({ direction: "in", peer: "+1555", body: "hello" });
  assert.equal(copy.title, "SMS from +1555");
  assert.equal(copy.body, "hello");
});
