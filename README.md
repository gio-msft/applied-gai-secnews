# GAI Security News
Collect security related news published in research spaces.

## Flow
1. Run grouped queries against arxiv API
2. Parse all feeds into managable bundle
3. Save all feeds with metadata
4. Download papers locally based on feeds
5. Process PDF to read content and process with GPT4
6. Save derived items back to paper metadata

# Sample
```
{
  "id": "2405.06823v2",
  "url": "http://arxiv.org/pdf/2405.06823v2.pdf",
  "published": "2024-05-10T21:52:34Z",
  "title": "PLeak: Prompt Leaking Attacks against Large Language Model Applications",
  "downloaded": true,
  "summarized": true,
  "shared": true,
  "emoji": "",
  "one_liner": "PLeak introduces a novel, highly effective method for leaking confidential prompts from LLM applications, challenging existing security defenses.",
  "points": [
    "PLeak, by optimizing adversarial queries, successfully reconstructs system prompts from LLM applications with high exact match and semantic similarity scores, outperforming prior works.",
    "The framework operates in a closed-box manner, making it significantly effective against LLMs without needing access to their internal parameters or architecture.",
    "PLeak demonstrates strong transferability across different LLMs and datasets, indicating its robustness and potential challenges in defending against such attacks."
  ]
}
```
