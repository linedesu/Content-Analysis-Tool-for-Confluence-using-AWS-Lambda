import json
import os
import urllib.request
import urllib.parse
import base64
import re
import html
import requests
from requests.auth import HTTPBasicAuth

# Environment variables
CONFLUENCE_EMAIL = os.getenv('CONFLUENCE_EMAIL')
CONFLUENCE_API_TOKEN = os.getenv('CONFLUENCE_API_TOKEN')
CONFLUENCE_BASE_URL = os.getenv('CONFLUENCE_BASE_URL')  # e.g., "https://your-confluence-instance.atlassian.net"
ATLASSIAN_ENDPOINT = os.getenv("ATLASSIAN_ENDPOINT")  # e.g., "https://your-confluence-instance.atlassian.net/wiki/api/v2/pages/atlassian_id?body-format=atlas_doc_format"
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")  # Slack webhook URL for notifications


def parse_table(html_content):
    """Extracts data from HTML tables."""
    table_regex = r'<table[^>]*>(.*?)</table>'
    row_regex = r'<tr[^>]*>(.*?)</tr>'
    cell_regex = r'<t[dh][^>]*>(.*?)</t[dh]>'

    table_data = []
    tables = re.findall(table_regex, html_content, re.DOTALL)

    for table in tables:
        rows = re.findall(row_regex, table, re.DOTALL)
        for row in rows:
            cells = re.findall(cell_regex, row, re.DOTALL)
            row_data = [re.sub(r'<.*?>', '', cell).strip() for cell in cells]
            if row_data:  # Ensure the row is not empty
                table_data.append(row_data)
    return table_data

def fetch_confluence_page(page_url):
    """Fetches content of a Confluence page by URL."""
    request = urllib.request.Request(page_url)

    # Basic authentication
    auth = f"{CONFLUENCE_USERNAME}:{CONFLUENCE_API_TOKEN}"
    base64_auth = base64.b64encode(auth.encode()).decode()
    request.add_header("Authorization", f"Basic {base64_auth}")

    try:
        with urllib.request.urlopen(request) as response:
            html_content = response.read().decode('utf-8')
            return html.unescape(html_content)
    except Exception as e:
        print(f"Error fetching page {page_url}: {e}")
        return None

def extract_topics_and_keywords(table_data):
    """Extracts topics and keywords from table data and organizes them into a dictionary."""
    topics_and_keywords = {}
    current_topic = None

    for row in table_data:
        # Skip rows that are headers or duplicates of the title row
        if len(row) > 0 and row[0].strip().lower() in ("curriculum topic", "keywords"):
            continue

        if len(row) > 0 and row[0].strip():  # New topic found
            current_topic = row[0].strip()
            topics_and_keywords[current_topic] = []

        if current_topic and len(row) > 1 and row[1].strip():  # Add keyword to current topic
            topics_and_keywords[current_topic].append(row[1].strip())
    
    return topics_and_keywords

def search_confluence(search_text, space_key):
    """Search Confluence for pages using CQL."""
    if not search_text.strip():
        print("Skipping empty term.")
        return []

    print(f"Searching Confluence for term: {search_text}...")
    search_url = f"{CONFLUENCE_BASE_URL}/wiki/rest/api/content/search"
    cql_query = f'title~"{search_text}" and space="{space_key}" and type="page"'
    params = {"cql": cql_query, "limit": 100}  # Adjust limit for batch size
    auth = f"{CONFLUENCE_USERNAME}:{CONFLUENCE_API_TOKEN}"
    base64_auth = base64.b64encode(auth.encode()).decode()
    results = []

    try:
        encoded_params = urllib.parse.urlencode(params)
        url = f"{search_url}?{encoded_params}"
        request = urllib.request.Request(url)
        request.add_header("Authorization", f"Basic {base64_auth}")

        with urllib.request.urlopen(request) as response:
            content = html.unescape(response.read().decode("utf-8"))
            search_result = json.loads(content)

            # Process results
            for page in search_result.get("results", []):
                results.append({
                    "url": f"{CONFLUENCE_BASE_URL}/wiki{page['_links']['webui']}",
                    "title": page["title"],
                    "id": page["id"]
                })
    except Exception as e:
        print(f"Error fetching results for term '{search_text}': {e}")

    print(f"Found {len(results)} pages for term '{search_text}'.")
    return results

    return topics_and_keywords

def get_document(atlassian_id):
    """Fetches the content of a Confluence page in atlas_doc_format using the atlassian_id."""
    url = ATLASSIAN_ENDPOINT.replace("atlassian_id", atlassian_id)
    auth = HTTPBasicAuth(CONFLUENCE_USERNAME, CONFLUENCE_API_TOKEN)
    headers = {"Accept": "application/json"}

    try:
        response = requests.get(url=url, headers=headers, auth=auth)
        if response.status_code != 200:
            print(f"Error fetching page {url}: HTTP {response.status_code} - {response.text}")
            return None

        data = response.json()
        if data.get("errors"):
            raise Exception(data["errors"][0]["title"])

        atlas_doc = json.loads(data["body"]["atlas_doc_format"]["value"])["content"]

        content_list = []
        for item in atlas_doc:
            if "content" in item and len(item["content"]) > 0:
                content_list.extend([text_item["text"] for text_item in item["content"] if "text" in text_item])

        page_content = " ".join(content_list).lower()
        print("\nFetched Document Content:\n========================\n")
        print(page_content)
        return page_content
    except Exception as e:
        print(f"Error fetching document with id {atlassian_id}: {e}")
        return None

def check_keyword_coverage(search_results, topics_and_keywords):
    """Checks if the extracted keywords are covered in the corresponding Confluence pages."""
    keyword_coverage = {}

    for topic, pages in search_results.items():
        keyword_coverage[topic] = []
        for page in pages:
            page_content = get_document(page["id"])
            if not page_content:
                continue

            covered_keywords = [kw for kw in topics_and_keywords[topic] if kw.lower() in page_content]
            missing_keywords = list(set(topics_and_keywords[topic]) - set(covered_keywords))

            keyword_coverage[topic].append({
                "title": page["title"],
                "url": page["url"],
                "covered_keywords": covered_keywords,
                "missing_keywords": missing_keywords
            })

    return keyword_coverage


def send_slack_notification(keyword_coverage):
    """Sends a notification to Slack with the keyword coverage results."""
    message = "*Curriculum Coverage Analysis Report:*\n\n"
    
    for topic, pages in keyword_coverage.items():
        if not pages:
            # No pages found for this topic
            message += f"*Topic:* {topic}\n- No pages found for this topic.\n\n"
            continue

        message += f"*Topic:* {topic}\n"
        for page in pages:
            message += (
                f"- *Page:* <{page['url']}|{page['title']}>\n"
                f"  - *Covered Keywords:* {', '.join(page['covered_keywords']) or 'None'}\n"
                f"  - *Missing Keywords:* {', '.join(page['missing_keywords']) or 'None'}\n"
            )
        message += "\n"

    # Handle the case where no topics were found
    if not keyword_coverage:
        message += "No topics were found in the curriculum.\n"

    payload = {"text": message}

    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload)
        if response.status_code != 200:
            print(f"Error sending Slack notification: {response.status_code} - {response.text}")
        else:
            print("Slack notification sent successfully.")
    except Exception as e:
        print(f"Error in Slack notification: {e}")


def lambda_handler(event, context):
    try:
        print("Lambda handler started...")
        # Extract the curriculum object from the event
        curriculum = event  # The Map state passes each curriculum object as raw input
        print("curriculum data received:", curriculum)
        
        # Extract curriculum details
        confluence_url = curriculum['page_link']
        space_key = curriculum['space_key']

        # Fetch Confluence page content
        print(f"Fetching Confluence page content from: {confluence_url}...")
        html_content = fetch_confluence_page(confluence_url)
        if not html_content:
            return {'statusCode': 500, 'body': json.dumps({'error': 'Failed to fetch Confluence page content'})}

        # Parse HTML tables and extract topics & keywords
        table_data = parse_table(html_content)
        topics_and_keywords = extract_topics_and_keywords(table_data)
        print("Extracted topics and keywords:", json.dumps(topics_and_keywords))

        # Search Confluence for each topic
        search_results = {topic: search_confluence(topic, space_key) for topic in topics_and_keywords.keys()}
        print("Search results:", json.dumps(search_results))

        # Check keyword coverage in found Confluence pages
        keyword_coverage = check_keyword_coverage(search_results, topics_and_keywords)
        print("Keyword Coverage:", json.dumps(keyword_coverage))

                # Send the keyword coverage report to Slack
        send_slack_notification(keyword_coverage)

        return {'statusCode': 200, 'body': json.dumps({'message': 'Keyword coverage report sent to Slack successfully.'})}
    except Exception as e:
        print(f"Error in lambda_handler: {e}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
