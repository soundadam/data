// The theme pick in the account card, as every console page has it
// (THEME_SCRIPT in platform-gitops' portals): THEME_COOKIE for the whole host.
(function () {
  var picks = document.querySelectorAll("[data-theme-pick]");
  var mark = function () {
    picks.forEach(function (pick) {
      pick.setAttribute("aria-pressed", String(pick.dataset.themePick === document.documentElement.dataset.theme));
    });
  };
  picks.forEach(function (pick) {
    pick.addEventListener("click", function () {
      document.documentElement.dataset.theme = pick.dataset.themePick;
      document.cookie = "theme=" + pick.dataset.themePick + "; Path=/; Max-Age=31536000; SameSite=Lax; Secure";
      mark();
    });
  });
  mark();
})();
