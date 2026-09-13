/* Landing hero — an ambient point field, drifting.
 *
 * The idea behind the visual: a ledger page seen edge-on. Points sit on
 * faint ruled lines and breathe with a slow travelling wave, so it reads
 * as something patient and periodic rather than a market ticker. All
 * geometry is generated here; there is no imported artwork or texture.
 *
 * This file is decoration and knows it. Everything it touches sits behind
 * the text in a container that already has a finished static background,
 * so the hero is complete before this runs, and complete if it never does.
 *
 * It declines to run at all when:
 *   - the reader asked for reduced motion
 *   - the device looks low-powered, or is on a metered/saver connection
 *   - the pointer is coarse and the screen is small (phones)
 *   - WebGL is unavailable, or Three.js failed to load
 * and it stops rendering whenever the tab is hidden or the hero is
 * scrolled out of view.
 */
(function () {
    "use strict";

    var stage = document.querySelector("[data-hero-stage]");
    if (!stage) return;

    // ---- gates ----------------------------------------------------------
    var mq = window.matchMedia;
    if (mq && mq("(prefers-reduced-motion: reduce)").matches) return;

    var conn = navigator.connection || {};
    if (conn.saveData) return;
    if (/2g/.test(conn.effectiveType || "")) return;

    var cores = navigator.hardwareConcurrency || 8;
    var memory = navigator.deviceMemory || 8;
    if (cores <= 4 || memory <= 4) return;

    var smallCoarse = mq
        && mq("(pointer: coarse)").matches
        && mq("(max-width: 900px)").matches;
    if (smallCoarse) return;

    if (typeof THREE === "undefined") return;

    var canvasTest = document.createElement("canvas");
    var hasWebGL = !!(window.WebGLRenderingContext
        && (canvasTest.getContext("webgl") || canvasTest.getContext("experimental-webgl")));
    if (!hasWebGL) return;

    // ---- scene ----------------------------------------------------------
    var INK = 0x16130f, MARK = 0xbf431d;
    var COLS = 96, ROWS = 34, SPREAD_X = 34, SPREAD_Y = 12;

    var renderer, scene, camera, points, rows, frame = null, running = false;
    var pointer = { x: 0, y: 0 }, eased = { x: 0, y: 0 }, clock = 0;

    try {
        renderer = new THREE.WebGLRenderer({ antialias: false, alpha: true, powerPreference: "low-power" });
    } catch (e) {
        return;
    }
    // Capping DPR is the single biggest win on a retina laptop: a 3x
    // buffer costs ~9x the fill rate for no visible gain on 2px dots.
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setClearColor(0x000000, 0);

    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(46, 1, 0.1, 120);
    camera.position.set(0, 3.4, 26);
    camera.lookAt(0, 0.4, 0);

    // Point field, laid out on a grid so the ruled-line reading survives.
    var count = COLS * ROWS;
    var positions = new Float32Array(count * 3);
    var colors = new Float32Array(count * 3);
    var seeds = new Float32Array(count);

    var inkC = new THREE.Color(INK), markC = new THREE.Color(MARK);
    var i = 0;
    for (var r = 0; r < ROWS; r++) {
        for (var c = 0; c < COLS; c++) {
            var x = (c / (COLS - 1) - 0.5) * SPREAD_X;
            var z = (r / (ROWS - 1) - 0.5) * SPREAD_Y * 2.4;
            positions[i * 3] = x;
            positions[i * 3 + 1] = 0;
            positions[i * 3 + 2] = z;
            seeds[i] = Math.random() * Math.PI * 2;

            // A sparse scatter of red marks, like corrections in a margin.
            var isMark = Math.random() < 0.014;
            var col = isMark ? markC : inkC;
            // Depth fade baked into vertex colour: cheaper than a shader,
            // and keeps the far edge from crowding the headline.
            var fade = 0.22 + 0.78 * (1 - Math.abs(z) / (SPREAD_Y * 1.2));
            colors[i * 3] = col.r * fade + (1 - fade) * 0.96;
            colors[i * 3 + 1] = col.g * fade + (1 - fade) * 0.94;
            colors[i * 3 + 2] = col.b * fade + (1 - fade) * 0.90;
            i++;
        }
    }

    var geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));

    var material = new THREE.PointsMaterial({
        size: 0.085,
        vertexColors: true,
        transparent: true,
        opacity: 0.9,
        sizeAttenuation: true,
        depthWrite: false
    });

    points = new THREE.Points(geometry, material);
    scene.add(points);

    // Two faint ruled lines, drawn rather than textured.
    rows = new THREE.Group();
    var lineMat = new THREE.LineBasicMaterial({ color: INK, transparent: true, opacity: 0.10 });
    [-2.6, 2.6].forEach(function (z) {
        var g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(new Float32Array([
            -SPREAD_X / 2, 0, z, SPREAD_X / 2, 0, z
        ]), 3));
        rows.add(new THREE.Line(g, lineMat));
    });
    scene.add(rows);

    stage.appendChild(renderer.domElement);
    renderer.domElement.setAttribute("aria-hidden", "true");

    function resize() {
        var w = stage.clientWidth, h = stage.clientHeight;
        if (!w || !h) return;
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
    }

    // The wave: two slow sine trains crossing. Periodic and unhurried —
    // it should never look like it is reacting to news.
    var pos = geometry.attributes.position;
    function animate(dt) {
        clock += dt;
        for (var n = 0; n < count; n++) {
            var x = positions[n * 3], z = positions[n * 3 + 2];
            pos.array[n * 3 + 1] =
                Math.sin(x * 0.22 + clock * 0.28 + seeds[n] * 0.12) * 0.85 +
                Math.sin(z * 0.34 - clock * 0.19) * 0.42;
        }
        pos.needsUpdate = true;

        // Cursor parallax, heavily eased so it feels like weight, not tracking.
        eased.x += (pointer.x - eased.x) * 0.025;
        eased.y += (pointer.y - eased.y) * 0.025;
        points.rotation.y = eased.x * 0.10;
        points.rotation.x = -0.30 + eased.y * 0.045;
        rows.rotation.copy(points.rotation);
    }

    var last = 0;
    function loop(now) {
        if (!running) return;
        frame = requestAnimationFrame(loop);
        var dt = Math.min((now - last) / 1000 || 0, 0.05);
        last = now;
        animate(dt);
        renderer.render(scene, camera);
    }

    function start() {
        if (running) return;
        running = true;
        last = performance.now();
        frame = requestAnimationFrame(loop);
    }
    function stop() {
        running = false;
        if (frame) cancelAnimationFrame(frame);
        frame = null;
    }

    window.addEventListener("resize", resize, { passive: true });

    window.addEventListener("pointermove", function (e) {
        pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
        pointer.y = (e.clientY / window.innerHeight) * 2 - 1;
    }, { passive: true });

    // Nothing renders behind a hidden tab.
    document.addEventListener("visibilitychange", function () {
        if (document.hidden) stop(); else start();
    });

    // Nor when the hero has been scrolled past.
    if ("IntersectionObserver" in window) {
        new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting && !document.hidden) start();
                else stop();
            });
        }, { threshold: 0.01 }).observe(stage);
    }

    resize();
    renderer.domElement.classList.add("is-ready");
    start();
})();
