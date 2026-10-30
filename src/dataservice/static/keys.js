// 密钥: issue (shown once), revoke, copy. Actions are same-origin JSON POSTs.
(function () {
  "use strict";
  const main = document.querySelector("main.console");
  const alert = document.createElement("p");
  alert.className = "hint";
  alert.setAttribute("role", "alert");
  alert.hidden = true;
  main.querySelector("#issue").append(alert);

  const say = (text) => {
    alert.textContent = text;
    alert.hidden = !text;
  };

  const post = async (path, body) => {
    const response = await fetch("/data/actions/" + path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error((await response.text()) || String(response.status));
    return response.json();
  };

  document.getElementById("mint").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    say("");
    try {
      const issued = await post("key", { label: document.getElementById("label").value.trim() });
      document.getElementById("key").value = issued.key;
      document.getElementById("minted").hidden = false;
      document.getElementById("issue").hidden = true;
    } catch (error) {
      say(error.message);
    } finally {
      button.disabled = false;
    }
  });

  main.addEventListener("click", async (event) => {
    const revoke = event.target.closest("[data-revoke]");
    if (revoke) {
      if (!window.confirm("作废这把密钥？用它的脚本会立刻失效。")) return;
      try {
        await post("revoke", { id: revoke.dataset.revoke });
        window.location.reload();
      } catch (error) {
        say(error.message);
      }
      return;
    }
    const copy = event.target.closest("[data-copy]");
    if (copy) {
      const field = document.getElementById(copy.dataset.copy);
      field.select();
      navigator.clipboard.writeText(field.value).then(() => {
        copy.textContent = "已复制";
      });
    }
  });
})();
