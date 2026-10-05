(function () {
  const slider = document.querySelector('[data-hero-slider]');
  if (!slider) return;
  const slides = [...slider.querySelectorAll('[data-hero-slide]')];
  const dots = [...slider.querySelectorAll('[data-hero-dot]')];
  if (slides.length < 2) return;
  let index = Math.max(0, slides.findIndex(slide => slide.classList.contains('is-active')));
  let timer;
  const show = next => {
    index = (next + slides.length) % slides.length;
    slides.forEach((slide, i) => { slide.classList.toggle('is-active', i === index); slide.setAttribute('aria-hidden', i === index ? 'false' : 'true'); });
    dots.forEach((dot, i) => { dot.classList.toggle('active', i === index); dot.setAttribute('aria-current', i === index ? 'true' : 'false'); });
  };
  const stop = () => window.clearInterval(timer);
  const start = () => { stop(); if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) timer = window.setInterval(() => show(index + 1), 6500); };
  slider.querySelector('[data-hero-prev]')?.addEventListener('click', () => { show(index - 1); start(); });
  slider.querySelector('[data-hero-next]')?.addEventListener('click', () => { show(index + 1); start(); });
  dots.forEach(dot => dot.addEventListener('click', () => { show(Number(dot.dataset.heroDot)); start(); }));
  slider.addEventListener('mouseenter', stop); slider.addEventListener('mouseleave', start);
  slider.addEventListener('focusin', stop); slider.addEventListener('focusout', event => { if (!slider.contains(event.relatedTarget)) start(); });
  document.addEventListener('visibilitychange', () => document.hidden ? stop() : start());
  show(index); start();
})();
