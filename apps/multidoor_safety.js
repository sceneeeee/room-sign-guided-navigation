() => {
    let epoch = 0;
    let generation = -1;
    let token = null;
    let latched = false;
    let acknowledgedAction = null;
    let expiredAction = null;
    let watchdog;
    let deadline = null;

    const advice = () => document.getElementById("roomnav-advice");
    const details = () => document.getElementById("roomnav-details");
    const image = () => document.getElementById("roomnav-marked");

    function clearDisplay(text) {
        clearTimeout(watchdog);
        deadline = null;
        if (advice()) advice().textContent = text;
        if (details()) details().textContent = "";
        if (image()) {
            image().hidden = true;
            image().removeAttribute("src");
        }
    }

    function latchStop(text) {
        latched = true;
        clearDisplay(text);
    }

    function expireDisplay() {
        if (!deadline) return;
        clearTimeout(watchdog);
        const remaining = deadline.time - performance.now();
        if (remaining > 0) {
            watchdog = setTimeout(expireDisplay, Math.ceil(remaining));
            return;
        }
        expiredAction = deadline.action;
        clearDisplay("STOP · Movement advice expired; awaiting session status");
    }

    function expireIfDue() {
        if (deadline && deadline.time <= performance.now()) expireDisplay();
    }

    window.addEventListener("offline", () => latchStop("STOPPED · Browser disconnected"));
    document.addEventListener("visibilitychange", expireDisplay);
    window.roomnavLive = {
        receive(packet) {
            const rendered = this.render(packet);
            const client = this.request("frame");
            if (rendered) {
                client.browser_update_ms = performance.now();
                client.rendered_token = packet.token;
            }
            return client;
        },
        request(kind) {
            if (kind === "done") expireIfDue();
            const cannotAcknowledge = kind === "done" && token?.action != null
                && [expiredAction, acknowledgedAction].includes(token.action);
            const requestToken = cannotAcknowledge || (latched && !["stop", "export", "poll"].includes(kind))
                ? null : token;
            if (kind === "reset") {
                epoch++;
                generation = -1;
                token = null;
                acknowledgedAction = null;
                expiredAction = null;
                latched = false;
                clearDisplay("Starting session");
            } else if (kind === "stop" || kind === "refuse") {
                latchStop("STOPPED · Stop requested");
            } else if (kind === "done" && requestToken?.action != null) {
                acknowledgedAction = requestToken.action;
                clearDisplay("WAIT_RGB · Feedback submitted");
            }
            return {epoch, generation, request_ms: performance.now(),
                    token: kind === "reset" ? null : requestToken};
        },
        render(packet) {
            if (!packet || packet.epoch !== epoch || !packet.token
                || packet.token.generation <= generation
                || (token && packet.token.session !== token.session)
                || (latched && !packet.terminal)) return;

            expireIfDue();
            generation = packet.token.generation;
            token = packet.token;
            const view = packet.view;
            if (packet.terminal) {
                latchStop(view.state + " · " + view.advice.reason);
                if (details()) details().textContent = JSON.stringify(view, null, 2);
                return true;
            }
            const lease = packet.lease;
            if (lease) {
                if (lease.action_id === acknowledgedAction || lease.action_id === expiredAction) return;
                // Translate server durations using the poll's browser start.
                // New RGB may refresh its watchdog, but never the action TTL.
                const sampledActionTime = packet.request_ms + packet.action_remaining_ms;
                const actionTime = deadline?.action === lease.action_id
                    ? Math.min(deadline.actionTime, sampledActionTime) : sampledActionTime;
                const time = Math.min(actionTime, packet.request_ms + packet.remaining_ms);
                deadline = {action: lease.action_id, time, actionTime};
                if (time <= performance.now()) {
                    expireDisplay();
                    return;
                }
                clearTimeout(watchdog);
                watchdog = setTimeout(expireDisplay, Math.ceil(time - performance.now()));
            } else {
                deadline = null;
                clearTimeout(watchdog);
            }
            if (advice()) {
                const action = lease?.kind ?? view.advice.action;
                advice().textContent = view.state + " · " + action + " · " + view.advice.reason;
            }
            if (details()) details().textContent = JSON.stringify(view, null, 2);
            if (image()) {
                image().hidden = !packet.image;
                if (packet.image) image().src = packet.image;
                else image().removeAttribute("src");
            }
            return true;
        }
    };
}
