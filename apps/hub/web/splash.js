/**
 * Splash Screen — AI for better Life 启动动画
 * 
 * 控制全屏启动动画的时序，页面加载时播放，
 * SPA 路由不会重新执行本脚本；整页刷新时重新播放。
 */

(function () {
  // 检查 DOM 中是否存在 splash 元素
  const splash = document.querySelector('.splash');
  if (!splash) return;

  // 检查用户是否偏好减少动画
  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const logo = splash.querySelector('.splash__logo');
  const title = splash.querySelector('.splash__title');

  if (!logo || !title) {
    // 缺少关键元素，直接移除遮罩
    splash.remove();
    return;
  }

  if (prefersReduced) {
    // 降级方案：仅 opacity 淡入淡出，总时长 800ms
    splash.style.transition = 'opacity 400ms';
    splash.style.opacity = '0';
    splash.offsetHeight; // 强制回流
    splash.style.opacity = '1';

    setTimeout(function () {
      splash.style.opacity = '0';
      splash.style.pointerEvents = 'none';
      setTimeout(function () { splash.remove(); }, 400);
    }, 800);
    return;
  }

  // 完整动画序列
  function animate() {
    // 阶段 1: 校徽弹入 (0ms)
    logo.style.animation = 'splashLogoIn 700ms cubic-bezier(.2,.8,.2,1) forwards';

    // 阶段 2: 标题淡入 (500ms)
    setTimeout(function () {
      title.style.animation = 'splashTitleIn 500ms ease-out forwards';
    }, 500);

    // 阶段 3: 校徽呼吸脉冲 (1600ms)
    setTimeout(function () {
      if (logo.style.animation) {
        logo.style.animation = 'splashPulse 600ms ease-in-out';
      }
    }, 1600);

    // 阶段 4: 整体淡出 (2200ms)
    setTimeout(function () {
      splash.style.animation = 'splashFadeOut 400ms ease forwards';
      splash.style.pointerEvents = 'none';
    }, 2200);

    // 阶段 5: 从 DOM 移除 (2600ms)
    setTimeout(function () {
      splash.remove();
    }, 2600);
  }

  // 校徽图片加载失败时切换为 CSS 占位圆徽
  var emblemImg = logo.querySelector('img');
  if (emblemImg) {
    if (emblemImg.complete) {
      animate();
    } else {
      emblemImg.addEventListener('load', animate);
      emblemImg.addEventListener('error', function () {
        // 创建 CSS 圆徽占位
        var fallback = document.createElement('div');
        fallback.className = 'splash__logo-fallback';
        fallback.textContent = 'USTC';
        logo.replaceWith(fallback);
        // 把引用更新到占位元素上
        if (splash.querySelector('.splash__inner')) {
          var inner = splash.querySelector('.splash__inner');
          var firstLogo = inner.querySelector('.splash__logo-fallback');
          if (firstLogo) {
            // 重新触发（此时 logo 已被替换，简化处理：直接开始动画）
            firstLogo.style.animation = 'splashLogoIn 700ms cubic-bezier(.2,.8,.2,1) forwards';
            setTimeout(function () {
              title.style.animation = 'splashTitleIn 500ms ease-out forwards';
            }, 500);
            setTimeout(function () {
              firstLogo.style.animation = 'splashPulse 600ms ease-in-out';
            }, 1600);
            setTimeout(function () {
              splash.style.animation = 'splashFadeOut 400ms ease forwards';
              splash.style.pointerEvents = 'none';
            }, 2200);
            setTimeout(function () {
              splash.remove();
            }, 2600);
          }
        }
      });
    }
  } else {
    animate();
  }
})();
