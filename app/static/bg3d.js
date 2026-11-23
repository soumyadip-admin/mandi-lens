/*
 * Ambient 3D background for Mandi Lens.
 *
 * A field of slow-drifting crop bars behind the page content. It is
 * deliberately cheap: one instanced mesh, no shadows, no textures, capped at
 * 30fps, paused when the tab is hidden, and switched off entirely for users
 * who ask for reduced motion. It reacts to the pointer with a gentle parallax
 * so the page feels alive without ever competing with the content.
 */
(function () {
  'use strict';

  var reduceMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduceMotion) return;

  // WebGL support check, and bail out quietly if absent.
  var probe = document.createElement('canvas');
  var hasGL = !!(probe.getContext('webgl') || probe.getContext('experimental-webgl'));
  if (!hasGL || !window.THREE) return;

  function boot() {
    var canvas = document.getElementById('bg-canvas');
    if (!canvas) return;

    var scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x0d1117, 0.055);

    var camera = new THREE.PerspectiveCamera(55, 1, 0.1, 120);
    camera.position.set(0, 3.4, 12);

    var renderer;
    try {
      renderer = new THREE.WebGLRenderer({
        canvas: canvas, antialias: false, alpha: true, powerPreference: 'low-power'
      });
    } catch (e) { return; }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));

    // --- lights: cheap, fixed -------------------------------------------
    scene.add(new THREE.AmbientLight(0x2b3d55, 1.1));
    var key = new THREE.DirectionalLight(0x4ea8ff, 0.75);
    key.position.set(-4, 8, 6);
    scene.add(key);
    var warm = new THREE.DirectionalLight(0x3fbf7f, 0.5);
    warm.position.set(7, 4, -5);
    scene.add(warm);

    // --- geometry: one instanced mesh, not N meshes ----------------------
    var COUNT = 220;
    var geo = new THREE.BoxGeometry(0.16, 1, 0.16);
    geo.translate(0, 0.5, 0);

    var mat = new THREE.MeshStandardMaterial({
      vertexColors: true, roughness: 0.55, metalness: 0.35
    });

    var mesh = new THREE.InstancedMesh(geo, mat, COUNT);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    scene.add(mesh);

    var palette = [0x3fbf7f, 0x4ea8ff, 0xe8c15a, 0x2f6f8f, 0x1f4a5c];
    var colors = new Float32Array(COUNT * 3);
    var bits = [];
    var SPREAD = 26;

    for (var i = 0; i < COUNT; i++) {
      var c = new THREE.Color(palette[i % palette.length]);
      colors[i * 3] = c.r; colors[i * 3 + 1] = c.g; colors[i * 3 + 2] = c.b;
      bits.push({
        x: (Math.random() - 0.5) * SPREAD,
        z: (Math.random() - 0.5) * SPREAD - 6,
        h: 0.5 + Math.random() * 3.6,
        phase: Math.random() * Math.PI * 2,
        speed: 0.25 + Math.random() * 0.5
      });
    }
    mesh.instanceColor = new THREE.InstancedBufferAttribute(colors, 3);

    var dummy = new THREE.Object3D();

    function layout(t) {
      for (var i = 0; i < COUNT; i++) {
        var b = bits[i];
        var sway = Math.sin(t * b.speed + b.phase) * 0.55;
        dummy.position.set(b.x + sway * 0.35, -1.4, b.z);
        dummy.scale.set(1, b.h + sway * 0.22, 1);
        dummy.rotation.y = b.phase + t * 0.12;
        dummy.updateMatrix();
        mesh.setMatrixAt(i, dummy.matrix);
      }
      mesh.instanceMatrix.needsUpdate = true;
    }

    // --- pointer parallax ------------------------------------------------
    var targetX = 0, targetY = 0, curX = 0, curY = 0;
    window.addEventListener('pointermove', function (e) {
      targetX = (e.clientX / window.innerWidth - 0.5) * 2;
      targetY = (e.clientY / window.innerHeight - 0.5) * 2;
    }, { passive: true });

    function resize() {
      var w = window.innerWidth, h = window.innerHeight;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
    window.addEventListener('resize', resize, { passive: true });
    resize();

    // --- render loop: throttled to ~30fps, paused when hidden ------------
    var running = true;
    var last = 0;
    var FRAME = 1000 / 30;

    document.addEventListener('visibilitychange', function () {
      running = !document.hidden;
      if (running) requestAnimationFrame(loop);
    });

    function loop(now) {
      if (!running) return;
      requestAnimationFrame(loop);
      if (now - last < FRAME) return;
      last = now;

      var t = now * 0.001;
      curX += (targetX - curX) * 0.03;
      curY += (targetY - curY) * 0.03;

      camera.position.x = curX * 1.6;
      camera.position.y = 3.4 + curY * 0.7;
      camera.lookAt(0, 1.1, -4);

      layout(t);
      renderer.render(scene, camera);
    }
    requestAnimationFrame(loop);

    // Expose a tiny handle so pages can dial the intensity down.
    window.MandiBG = {
      setOpacity: function (v) { canvas.style.opacity = String(v); }
    };
  }

  if (window.THREE) boot();
  else window.addEventListener('load', boot);
})();
