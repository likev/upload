#!/usr/bin/env node
import process from "process";
import { Scraper } from "@the-convocation/twitter-scraper";
import { cycleTLSFetch, cycleTLSExit } from "@the-convocation/twitter-scraper/cycletls";
import stringify from "json-stable-stringify";
import { Headers } from "headers-polyfill";

const url = process.argv[2];
if (!url) {
  console.error("Usage: twitter_article_fetch.mjs <url>");
  process.exit(2);
}

const cookie = process.env.TWITTER_COOKIES || process.env.TWITTER_COOKIE;

function normalizeUrlForId(value) {
  try {
    const parsed = new URL(value);
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString();
  } catch {
    return value;
  }
}

function extractTweetId(value) {
  try {
    const parsed = new URL(normalizeUrlForId(value));
    const parts = parsed.pathname.split("/").filter(Boolean);
    const statusIndex = parts.indexOf("status");
    if (statusIndex >= 0 && parts[statusIndex + 1]) {
      return parts[statusIndex + 1];
    }
  } catch {
    return null;
  }
  return null;
}

function extractArticleId(value) {
  try {
    const parsed = new URL(normalizeUrlForId(value));
    const parts = parsed.pathname.split("/").filter(Boolean);
    const articlesIndex = parts.indexOf("articles");
    if (articlesIndex >= 0 && parts[articlesIndex + 1]) {
      return parts[articlesIndex + 1];
    }
    const articleIndex = parts.indexOf("article");
    if (articleIndex >= 0 && parts[articleIndex + 1]) {
      return parts[articleIndex + 1];
    }
  } catch {
    return null;
  }
  return null;
}

function isArticleUrl(value) {
  return Boolean(extractArticleId(value));
}

function normalizeCookies(raw) {
  if (!raw) return [];
  return raw
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean);
}

function extractArticleIdFromUrl(value) {
  try {
    const parsed = new URL(value);
    const parts = parsed.pathname.split("/").filter(Boolean);
    const articlesIndex = parts.indexOf("articles");
    if (articlesIndex >= 0 && parts[articlesIndex + 1]) {
      return parts[articlesIndex + 1];
    }
    const articleIndex = parts.indexOf("article");
    if (articleIndex >= 0 && parts[articleIndex + 1]) {
      return parts[articleIndex + 1];
    }
  } catch {
    return null;
  }
  return null;
}

function findArticleIdFromTweet(tweet) {
  const urls = Array.isArray(tweet?.urls) ? tweet.urls : [];
  for (const raw of urls) {
    const id = extractArticleIdFromUrl(raw);
    if (id) return id;
  }
  return null;
}

const TWEET_RESULT_BY_REST_ID_EXAMPLE =
  "https://api.x.com/graphql/4PdbzTmQ5PTjz9RiureISQ/TweetResultByRestId?variables=%7B%22tweetId%22%3A%221985465713096794294%22%2C%22includePromotedContent%22%3Atrue%2C%22withBirdwatchNotes%22%3Atrue%2C%22withVoice%22%3Atrue%2C%22withCommunity%22%3Atrue%7D&features=%7B%22creator_subscriptions_tweet_preview_api_enabled%22%3Atrue%2C%22premium_content_api_read_enabled%22%3Afalse%2C%22communities_web_enable_tweet_community_results_fetch%22%3Atrue%2C%22c9s_tweet_anatomy_moderator_badge_enabled%22%3Atrue%2C%22responsive_web_grok_analyze_button_fetch_trends_enabled%22%3Afalse%2C%22responsive_web_grok_analyze_post_followups_enabled%22%3Atrue%2C%22responsive_web_jetfuel_frame%22%3Atrue%2C%22responsive_web_grok_share_attachment_enabled%22%3Atrue%2C%22responsive_web_grok_annotations_enabled%22%3Atrue%2C%22articles_preview_enabled%22%3Atrue%2C%22responsive_web_edit_tweet_api_enabled%22%3Atrue%2C%22graphql_is_translatable_rweb_tweet_is_translatable_enabled%22%3Atrue%2C%22view_counts_everywhere_api_enabled%22%3Atrue%2C%22longform_notetweets_consumption_enabled%22%3Atrue%2C%22responsive_web_twitter_article_tweet_consumption_enabled%22%3Atrue%2C%22tweet_awards_web_tipping_enabled%22%3Afalse%2C%22responsive_web_grok_show_grok_translated_post%22%3Atrue%2C%22responsive_web_grok_analysis_button_from_backend%22%3Atrue%2C%22post_ctas_fetch_enabled%22%3Atrue%2C%22freedom_of_speech_not_reach_fetch_enabled%22%3Atrue%2C%22standardized_nudges_misinfo%22%3Atrue%2C%22tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled%22%3Atrue%2C%22longform_notetweets_rich_text_read_enabled%22%3Atrue%2C%22longform_notetweets_inline_media_enabled%22%3Atrue%2C%22profile_label_improvements_pcf_label_in_post_enabled%22%3Atrue%2C%22responsive_web_profile_redirect_enabled%22%3Afalse%2C%22rweb_tipjar_consumption_enabled%22%3Afalse%2C%22verified_phone_label_enabled%22%3Afalse%2C%22responsive_web_grok_image_annotation_enabled%22%3Atrue%2C%22responsive_web_grok_imagine_annotation_enabled%22%3Atrue%2C%22responsive_web_grok_community_note_auto_translation_is_enabled%22%3Afalse%2C%22responsive_web_graphql_skip_user_profile_image_extensions_enabled%22%3Afalse%2C%22responsive_web_graphql_timeline_navigation_enabled%22%3Atrue%2C%22responsive_web_enhance_cards_enabled%22%3Afalse%7D&fieldToggles=%7B%22withArticleRichContentState%22%3Atrue%2C%22withArticlePlainText%22%3Afalse%7D";

const BEARER_TOKEN_2 =
  "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA";

class ApiRequest {
  constructor({ url, variables, features, fieldToggles }) {
    this.url = url;
    this.variables = variables;
    this.features = features;
    this.fieldToggles = fieldToggles;
  }
  toRequestUrl() {
    const params = new URLSearchParams();
    if (this.variables) {
      const variablesStr = stringify(this.variables);
      if (variablesStr) params.set("variables", variablesStr);
    }
    if (this.features) {
      const featuresStr = stringify(this.features);
      if (featuresStr) params.set("features", featuresStr);
    }
    if (this.fieldToggles) {
      const fieldTogglesStr = stringify(this.fieldToggles);
      if (fieldTogglesStr) params.set("fieldToggles", fieldTogglesStr);
    }
    return `${this.url}?${params.toString()}`;
  }
}

function parseEndpointExample(example) {
  const parsed = new URL(example);
  const base = `${parsed.protocol}//${parsed.host}${parsed.pathname}`;
  const variables = parsed.searchParams.get("variables");
  const features = parsed.searchParams.get("features");
  const fieldToggles = parsed.searchParams.get("fieldToggles");
  return new ApiRequest({
    url: base,
    variables: variables ? JSON.parse(variables) : undefined,
    features: features ? JSON.parse(features) : undefined,
    fieldToggles: fieldToggles ? JSON.parse(fieldToggles) : undefined,
  });
}

function createTweetResultByRestIdRequest() {
  return parseEndpointExample(TWEET_RESULT_BY_REST_ID_EXAMPLE);
}

async function requestApi(url, auth, bearerTokenOverride) {
  const headers = new Headers();
  await auth.installTo(headers, url, bearerTokenOverride);
  const res = await auth.fetch(url, { method: "GET", headers, credentials: "include" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API request failed: ${res.status} ${text.slice(0, 200)}`);
  }
  return await res.json();
}

async function getArticleAnonymous(id, auth) {
  const tweetResultByRestIdRequest = createTweetResultByRestIdRequest();
  tweetResultByRestIdRequest.variables.tweetId = id;
  const data = await requestApi(
    tweetResultByRestIdRequest.toRequestUrl(),
    auth,
    BEARER_TOKEN_2
  );
  if (!data || !data.data) {
    return null;
  }
  return data.data;
}

try {
  if (isArticleUrl(url)) {
    const articleId = extractArticleId(url);
    const scraper = new Scraper({ fetch: cycleTLSFetch });
    if (cookie) {
      await scraper.setCookies(normalizeCookies(cookie));
    }
    const data = articleId ? await getArticleAnonymous(articleId, scraper.auth) : null;
    if (data) {
      console.log(JSON.stringify({ data }));
      await cycleTLSExit();
      process.exit(0);
    }
  }

  const id = extractTweetId(url);
  if (!id) {
    console.error("Unable to extract tweet id from URL");
    await cycleTLSExit();
    process.exit(1);
  }

  const scraper = new Scraper({ fetch: cycleTLSFetch });
  if (cookie) {
    await scraper.setCookies(normalizeCookies(cookie));
  }

  const tweet = await scraper.getTweet(id);
  if (!tweet) {
    console.error("Tweet not found");
    await cycleTLSExit();
    process.exit(1);
  }

  console.log(tweet);

  const articleData = await getArticleAnonymous(id, scraper.auth);
  if (articleData && articleData.tweetResult && Object.keys(articleData.tweetResult).length > 0) {
    console.log(JSON.stringify({ data: articleData, articleId: id }));
    await cycleTLSExit();
    process.exit(0);
  }

  const linkedArticleId = findArticleIdFromTweet(tweet);
  if (linkedArticleId) {
    const data = await getArticleAnonymous(linkedArticleId, scraper.auth);
    if (data) {
      console.log(JSON.stringify({ data, articleId: linkedArticleId }));
      await cycleTLSExit();
      process.exit(0);
    }
  }

  const title = tweet.article?.title || tweet.note_tweet?.title || "";
  const body =
    tweet.article?.text ||
    tweet.article?.body ||
    tweet.note_tweet?.text ||
    tweet.full_text ||
    tweet.text ||
    "";

  const payload = { title, body };
  console.log(JSON.stringify(payload));
  await cycleTLSExit();
} catch (err) {
  console.error(err && err.message ? err.message : String(err));
  try {
    await cycleTLSExit();
  } catch {
    // ignore cleanup errors
  }
  process.exit(1);
}
