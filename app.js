const feedUrl = 'https://zenseeknews-44de98dc7b2b.herokuapp.com/api/v1/trends/daily';
const featured = document.querySelector('#featured');
const grid = document.querySelector('#news-grid');
const status = document.querySelector('#status');
const empty = document.querySelector('#empty');
const lastUpdated = document.querySelector('#last-updated');

function showLastUpdated(value) {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) {
    lastUpdated.textContent = 'Unavailable';
    lastUpdated.removeAttribute('datetime');
    return;
  }
  const date = new Date(timestamp);
  lastUpdated.dateTime = date.toISOString();
  lastUpdated.textContent = new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric',
    minute: '2-digit', timeZoneName: 'short'
  }).format(date);
}

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
  const image = [item.image, ...(item.sources || []).map(source => source.image)]
    .find(url => typeof url === 'string' && /^https?:\/\//i.test(url)
      && !/^https?:\/\/(?:source\.)?unsplash\.com\//i.test(url));
  if (!image) return null;
  const container = element('div', 'story-image');
  const img = document.createElement('img');
  img.src = image;
  img.alt = '';
  img.loading = 'lazy';
  img.addEventListener('error', () => {
    const parent = container.parentElement;
    container.remove();
    parent?.classList.remove('has-image');
  });
  container.append(img);
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
  const link = element('a', 'read-link', item.content ? 'READ FULL STORY →' : 'VIEW SOURCE REPORTS →');
  link.href = storyUrl(item);
  copy.append(
    element('div', 'feature-kicker', item.category || 'FEATURED STORY'),
    storyMeta(item),
    element('h3', '', item.headline || item.title || item.topic),
    element('p', 'feature-description', item.content ? item.description || '' : ''),
    link
  );
  card.append(copy);
  const image = storyImage(item);
  if (image) {
    card.classList.add('has-image');
    card.append(image);
  }
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
    element('p', 'card-description', item.content ? item.description || '' : ''),
    element('span', 'card-read', item.content ? 'READ STORY →' : 'VIEW SOURCE REPORTS →')
  );
  const image = storyImage(item);
  if (image) link.append(image);
  link.append(copy);
  card.append(link);
  return card;
}

async function loadStories() {
  try {
    const response = await fetch(feedUrl, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error(`News feed returned ${response.status}`);
    const data = await response.json();
    showLastUpdated(data.updated_at);
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
    empty.hidden = true;
    grid.replaceChildren(...items.slice(1, 40).map(storyCard));
    const updatedAt = Date.parse(data.updated_at);
    const isStale = !Number.isFinite(updatedAt) || Date.now() - updatedAt > 60 * 60 * 1000;
    const hasArchivedStories = items.some(item => !item.content);
    status.textContent = isStale
      ? 'News updates are delayed; these stories may be out of date.'
      : hasArchivedStories
        ? 'Some stories link to source reports while full briefings are unavailable.'
        : `${items.length} stories in today’s briefing`;
    const majorList = document.querySelector('#major-list');
    majorList.replaceChildren();
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
    if (!lastUpdated.dateTime) showLastUpdated(null);
    featured.textContent = 'The news feed is unavailable right now.';
    status.textContent = 'Unable to load stories.';
    empty.hidden = false;
    empty.textContent = 'Please try again later.';
  }
}

async function loadNewsletter() {
  const message = document.querySelector('#newsletter-status');
  try {
    const response = await fetch('https://zenseeknews-44de98dc7b2b.herokuapp.com/api/v1/newsletter', {
      headers: { Accept: 'application/json' }, cache: 'no-store'
    });
    if (!response.ok) throw new Error(`Newsletter configuration returned ${response.status}`);
    const { signup_url: signupUrl } = await response.json();
    if (!signupUrl) {
      message.textContent = 'Newsletter signup is not available yet.';
      return;
    }
    const url = new URL(signupUrl);
    if (url.protocol !== 'https:' || !(url.hostname === 'sibforms.com' || url.hostname.endsWith('.sibforms.com'))) {
      throw new Error('Invalid newsletter signup URL');
    }
    document.querySelector('#newsletter-form').src = url.href;
    document.querySelector('#newsletter-link').href = url.href;
    document.querySelector('#newsletter-signup').hidden = false;
    message.textContent = 'Sign up below to receive the daily briefing.';
  } catch (error) {
    console.error('Unable to load newsletter signup:', error);
    message.textContent = 'Newsletter signup is unavailable right now.';
  }
}

loadStories();
loadNewsletter();
setInterval(loadStories, 60 * 1000);

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(error => {
      console.error('Unable to register the service worker:', error);
    });
  });
}
