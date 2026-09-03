const statusElement = document.querySelector("#status");
const baseUrlInput = document.querySelector("#base-url");
const saveButton = document.querySelector("#save");

function refresh() {
  chrome.runtime.sendMessage({ type: "status" }, (response) => {
    if (chrome.runtime.lastError || !response) {
      renderStatus("error");
      return;
    }
    baseUrlInput.value = response.baseUrl;
    renderStatus(response.state);
  });
}

function renderStatus(state) {
  statusElement.className = `status ${state}`;
  statusElement.textContent = state === "connected"
    ? "Connected to Jobfeed"
    : `Bridge ${state}`;
}

saveButton.addEventListener("click", () => {
  saveButton.disabled = true;
  chrome.runtime.sendMessage(
    { type: "configure", baseUrl: baseUrlInput.value },
    (response) => {
      saveButton.disabled = false;
      if (chrome.runtime.lastError || !response) {
        renderStatus("error");
        return;
      }
      baseUrlInput.value = response.baseUrl;
      renderStatus(response.state);
      setTimeout(refresh, 500);
    },
  );
});

refresh();
