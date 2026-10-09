const chat = document.getElementById("chat");
const form = document.getElementById("ask-form");
const input = document.getElementById("question-input");
const button = document.getElementById("ask-button");

// Flask backend
const API_BASE = "";

// Holds state while we're waiting on a clarification answer
let pendingClarification = null; // { originalQuestion, clarificationQuestion }

let lastUserQuestion = "";

function addBubble(text, cls) {
  const div = document.createElement("div");
  div.className = `bubble ${cls}`;
  div.textContent = text;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

function addClarificationBubble(question, options) {
  const div = document.createElement("div");
  div.className = "bubble clarify";

  const title = document.createElement("div");
  title.className = "clarify-title";
  title.textContent = `⚠️ Clarification needed: ${question}`;
  div.appendChild(title);

  if (options && options.length) {
    const wrap = document.createElement("div");
    wrap.className = "options";

    options.forEach((opt) => {
      const btn = document.createElement("button");
      btn.className = "option-btn";
      btn.type = "button";
      btn.textContent = opt;

      btn.addEventListener("click", () => {
        submitClarificationAnswer(opt);
      });

      wrap.appendChild(btn);
    });

    div.appendChild(wrap);
  }

  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function addSuccessBubble(answer, sql, rowCount) {
  const div = document.createElement("div");
  div.className = "bubble success";
  div.textContent = answer;

  const toggle = document.createElement("div");
  toggle.className = "sql-toggle";
  toggle.textContent = `View generated SQL (${rowCount} row${
    rowCount === 1 ? "" : "s"
  })`;

  const code = document.createElement("pre");
  code.className = "sql-code";
  code.textContent = sql;

  toggle.addEventListener("click", () => {
    code.classList.toggle("open");
  });

  div.appendChild(toggle);
  div.appendChild(code);

  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

async function callApi(payload) {
  const res = await fetch(`${API_BASE}/api/ask`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  let data;

  try {
    data = await res.json();
  } catch {
    throw new Error(
      `The backend returned an invalid response (HTTP ${res.status}).`
    );
  }

  // Preserve the backend's error message, including intentional HTTP 400 responses.
  if (!res.ok) {
    throw new Error(
      data.message ||
        data.error ||
        `The backend returned HTTP ${res.status}.`
    );
  }

  return data;
}

async function handleResponse(data) {
  if (data.status === "needs_clarification") {
    pendingClarification = {
      originalQuestion:
        pendingClarification?.originalQuestion || lastUserQuestion,
      clarificationQuestion: data.clarification_question,
    };

    addClarificationBubble(
      data.clarification_question,
      data.options
    );
  } else if (data.status === "success") {
    pendingClarification = null;

    addSuccessBubble(
      data.answer,
      data.sql,
      data.row_count ?? 0
    );
  } else {
    pendingClarification = null;

    addBubble(
      data.message || "Something went wrong.",
      "error"
    );
  }
}

async function submitClarificationAnswer(answerText) {
  if (!pendingClarification) {
    addBubble(
      "Please ask your question again before selecting a clarification.",
      "error"
    );
    return;
  }

  addBubble(answerText, "user");

  const loading = addBubble("Thinking…", "loading");

  button.disabled = true;

  try {
    const data = await callApi({
      question: pendingClarification.originalQuestion,

      clarification: {
        original_question:
          pendingClarification.originalQuestion,

        clarification_question:
          pendingClarification.clarificationQuestion,

        user_answer: answerText,
      },
    });

    loading.remove();

    await handleResponse(data);
  } catch (err) {
    console.error("Clarification API error:", err);

    loading.remove();

    addBubble(
      err.message ||
        "Unable to contact the backend. Please check your connection and try again.",
      "error"
    );
  } finally {
    button.disabled = false;
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const question = input.value.trim();

  if (!question) {
    return;
  }

  lastUserQuestion = question;

  addBubble(question, "user");

  input.value = "";

  button.disabled = true;

  const loading = addBubble("Thinking…", "loading");

  try {
    const data = await callApi({
      question: question,
    });

    loading.remove();

    await handleResponse(data);
  } catch (err) {
    console.error("API error:", err);

    loading.remove();

    addBubble(
      err.message ||
        "Unable to contact the backend. Please check your connection and try again.",
      "error"
    );
  } finally {
    button.disabled = false;
  }
});