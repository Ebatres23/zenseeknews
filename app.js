const featured = document.querySelector("#featured");
const grid = document.querySelector("#news-grid");
const status = document.querySelector("#status");
const empty = document.querySelector("#empty");
const sort = document.querySelector("#sort");
let articles = [];

document.querySelector("#today").textContent = new Intl.DateTimeFormat(undefined, {
  weekday: "long", month: "short", day: "numeric"
}).format(new Date()).toUpperCase();

const menu = document.querySelector(".menu-toggle");
const navigation = document.querySelector("#navigation");
menu.addEventListener("click", () => {
  const open = menu.getAttribute("aria-expanded") !== "true";
  menu.setAttribute("aria-expanded", String(open));
  menu.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
  navigation.classList.toggle("open", open);
});
navigation.addEventListener("click", (event) => {
  if (event.target.closest("a")) {
    navigation.classList.remove("open");
    menu.setAttribute("aria-expanded", "false");
    menu.setAttribute("aria-label", "Open navigation");
  }
});

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : "";
  } catch {
    return "";
  }
}

function imageFor(article) {
  const container = element("div", "story-image");
  const fallback = element("div", "image-fallback");
  fallback.setAttribute("aria-hidden", "true");
  fallback.innerHTML = '<svg viewBox="0 0 240 160" xmlns="http://www.w3.org/2000/svg"><circle cx="120" cy="80" r="52" fill="none" stroke="currentColor" stroke-width="1"/><circle cx="120" cy="80" r="35" fill="none" stroke="currentColor" stroke-width="1"/><path d="M25 80h190M120 0v160M53 13l134 134M187 13L53 147" fill="none" stroke="currentColor" stroke-width="1"/><circle cx="120" cy="80" r="8" fill="currentColor"/></svg>';
  container.append(fallback);
  const imageUrl = safeUrl(article.image);
  if (imageUrl) {
    const image = document.createElement("img");
    image.src = imageUrl;
    image.alt = "";
    image.loading = "lazy";
    image.addEventListener("error", () => image.remove());
    container.append(image);
  }
  return container;
}

function dateLabel(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Recently";
  return new Intl.DateTimeFormat(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit"
  }).format(date);
}

function storyLink(article, className) {
  const link = element("a", className);
  link.href = safeUrl(article.url);
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.addEventListener("click", () => {
    fetch(`/api/articles/${encodeURIComponent(article.id)}/view`, {
      method: "POST", keepalive: true
    }).catch(() => {});
  });
  return link;
}

function storyMeta(article) {
  const meta = element("div", "story-meta");
  meta.append(element("span", "source", article.source), element("span", "meta-dot", "•"));
  const time = element("time", "", dateLabel(article.published_at));
  time.dateTime = article.published_at;
  meta.append(time);
  return meta;
}

function render() {
  grid.replaceChildren();
  const valid = articles.filter((article) => safeUrl(article.url) && article.title);
  valid.sort((a, b) => new Date(b.published_at) - new Date(a.published_at));
  const top = valid[0];
  if (!top) {
    featured.className = "featured-placeholder";
    featured.textContent = "Your top story will appear here as soon as the news feed is available.";
    empty.hidden = false;
    empty.textContent = "No stories just yet. We’re checking our sources and will try again shortly.";
    return;
  }
  empty.hidden = true;
  const featureLink = storyLink(top, "featured-card");
  const featureCopy = element("div", "featured-copy");
  featureCopy.append(element("span", "feature-kicker", "✳  THE STORY TO KNOW"), storyMeta(top));
  featureCopy.append(element("h3", "", top.title));
  featureCopy.append(element("p", "feature-description", top.description || "Read the full story from the original source."));
  featureCopy.append(element("span", "read-link", "READ THE STORY  ↗"));
  featureLink.append(featureCopy, imageFor(top));
  featured.className = "";
  featured.replaceChildren(featureLink);

  const remaining = valid.slice(1);
  if (sort.value === "popular") {
    remaining.sort((a, b) => (b.views || 0) - (a.views || 0) || new Date(b.published_at) - new Date(a.published_at));
  }
  for (const article of remaining) {
    const card = element("article", "news-card");
    const link = storyLink(article, "card-link");
    const copy = element("div", "card-copy");
    copy.append(storyMeta(article), element("h3", "", article.title));
    copy.append(element("p", "card-description", article.description || "Read the full story from the original source."));
    copy.append(element("span", "card-read", "READ STORY  ↗"));
    link.append(imageFor(article), copy);
    card.append(link);
    grid.append(card);
  }
}

async function updateNews() {
  try {
    const response = await fetch("/api/news", { cache: "no-store" });
    if (!response.ok) throw new Error("News feed unavailable");
    const data = await response.json();
    articles = Array.isArray(data.articles) ? data.articles : [];
    render();
    status.textContent = data.last_updated
      ? `●  LIVE FEED  ·  Last updated ${dateLabel(data.last_updated)}  ·  Refreshes every minute`
      : "●  CONNECTING  ·  Checking our sources for the latest stories";
  } catch {
    status.textContent = "●  CONNECTION INTERRUPTED  ·  Retrying in a minute";
    if (!articles.length) render();
  }
}

sort.addEventListener("change", render);
updateNews();
setInterval(updateNews, 60000);
