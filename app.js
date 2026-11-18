const feedUrl = 'https://zenseeknews-44de98dc7b2b.herokuapp.com/api/v1/trends/daily';
const featured = document.querySelector('#featured');
const grid = document.querySelector('#news-grid');
const status = document.querySelector('#status');
const empty = document.querySelector('#empty');

document.querySelector('#today').textContent = new Intl.DateTimeFormat('en-US', {
  month: 'short', day: 'numeric', year: 'numeric'
}).format(new Date()).toUpperCase();

const menu = document.querySelector('.menu-toggle');
const navigation = document.querySelector('#navigation');
menu.addEventListener('click', () => {
  const open = navigation.classList.toggle('open');
  menu.setAttribute('aria-expanded', String(open));
  menu.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
});
navigation.addEventListener('click', event => {
  if (event.target.closest('a')) {
    navigation.classList.remove('open');
    menu.setAttribute('aria-expanded', 'false');
    menu.setAttribute('aria-label', 'Open navigation');
  }
});

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function articleDate(item) {
  const raw = item.published_at || item.published;
  if (!raw) return 'Date unavailable';
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return 'Date unavailable';
  return new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC'
  }).format(date);
}

function storyImage(item) {
  const container = element('div', 'story-image');
  const fallback = element('div', 'image-fallback', '✳');
  fallback.setAttribute('aria-hidden', 'true');
  container.append(fallback);
  const image = item.image || item.sources?.[0]?.image;
  if (image && /^(https?:\/\/|data:image\/)/i.test(image)) {
    const img = document.createElement('img');
    img.src = image;
    img.alt = '';
    img.loading = 'lazy';
    img.addEventListener('error', () => img.remove());
    container.append(img);
  }
  return container;
}

function storyMeta(item) {
  const meta = element('div', 'story-meta');
  meta.append(
    element('span', 'source', item.sources?.[0]?.source || item.category || 'News'),
    element('span', 'meta-dot', '·'),
    element('span', '', articleDate(item))
  );
  return meta;
}

function storyUrl(item) {
  return `/story/${encodeURIComponent(item.slug)}`;
}

function featuredStory(item) {
  const card = element('article', 'featured-card');
  const copy = element('div', 'featured-copy');
  const link = element('a', 'read-link', 'READ FULL STORY →');
  link.href = storyUrl(item);
  copy.append(
    element('div', 'feature-kicker', item.category || 'FEATURED STORY'),
    storyMeta(item),
    element('h3', '', item.headline || item.title || item.topic),
    element('p', 'feature-description', item.description || ''),
    link
  );
  card.append(copy, storyImage(item));
  return card;
}

function storyCard(item) {
  const card = element('article', 'news-card');
  const link = element('a', 'card-link');
  link.href = storyUrl(item);
  const copy = element('div', 'card-copy');
  copy.append(
    storyMeta(item),
    element('h3', '', item.headline || item.title || item.topic),
    element('p', 'card-description', item.description || ''),
    element('span', 'card-read', 'READ STORY →')
  );
  link.append(storyImage(item), copy);
  card.append(link);
  return card;
}

async function loadStories() {
  try {
    const response = await fetch(feedUrl, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error(`News feed returned ${response.status}`);
    const data = await response.json();
    const items = (data.items || []).filter(item => item.slug).sort((a, b) => {
      const aTime = Date.parse(a.published_at || a.published) || 0;
      const bTime = Date.parse(b.published_at || b.published) || 0;
      return bTime - aTime || (parseInt(b.traffic, 10) || 0) - (parseInt(a.traffic, 10) || 0);
    });
    if (!items.length) {
      featured.textContent = 'No stories are available yet.';
      status.textContent = 'No stories available.';
      empty.hidden = false;
      empty.textContent = 'Please check back for the next update.';
      return;
    }
    featured.replaceChildren(featuredStory(items[0]));
    featured.classList.remove('featured-placeholder');
    grid.replaceChildren(...items.slice(1, 40).map(storyCard));
    status.textContent = `${items.length} stories in today’s briefing`;
    const majorList = document.querySelector('#major-list');
    const majorStories = (data.major_news || []).filter(story => /^https?:\/\//i.test(story.url || '')).slice(0, 15);
    for (const story of majorStories) {
      const row = document.createElement('li');
      const link = element('a', '', story.title);
      link.href = story.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      row.append(link, element('small', '', `${story.source || 'Source'} · ${story.leaning || 'Mixed'}`));
      majorList.append(row);
    }
    document.querySelector('#major-news').hidden = !majorStories.length;
    if (items.length === 1) {
      empty.hidden = false;
      empty.textContent = 'More stories will appear here when available.';
    }
  } catch (error) {
    console.error('Unable to load the news feed:', error);
    featured.textContent = 'The news feed is unavailable right now.';
    status.textContent = 'Unable to load stories.';
    empty.hidden = false;
    empty.textContent = 'Please try again later.';
  }
}

loadStories();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(error => {
      console.error('Unable to register the service worker:', error);
    });
  });
}
