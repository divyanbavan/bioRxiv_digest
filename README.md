# bioRxiv digest 🧬
Automatically have papers from bioRxiv sent to your email at 8am ET, personalized to your interests. Each email also explains a new idea, general concept, and interest-based concept so you can continue building up your knowledge base.

Each email contains:

- Five papers selected based on user interests, scientific impact, and robustness
- An idea that is made possible by the results of one or more of the papers
- AI explanation of a general concept from any field, meant to boost breadth knowledge
- AI explanation of a concept in your interests, meant to boost depth knowledge

This digest creates AI summaries of each paper (using Gemini) to get a brief overview of the paper. Abstracts and links to the full paper are also available for further exploration.

## Instructions
To setup the email digest, please follow the steps below

### Setting up the repository

1. Click 'Use this template' (button is above the 'About' section)
2. Choose a name for the repository and set visibility to private

### Getting a Gemini API Key
To get a free Gemini API key, you need to use Google AI Studio. Make sure to keep this API key private.

1. Go to [Google AI Studio](https://aistudio.google.com/) and sign into the platform
2. Go to 'Dashboard' (find this on the left hand side panel)
3. You should have a default API key automatically given by Google. This can be used for running the digest. See the image below for getting the API key.

![API Key Information](./API_KEY_INFORMATION.png)


### Setting up Gmail routing
To enable this setting, **make sure you have 2 factor authentication enabled for the Gmail account you wish to use**.

1. Go to your [Google Account settings](https://myaccount.google.com/)
2. In the search bar, type in 'App Passwords' and select it
3. Confirm it's you through 2 factor authentication
4. Type in 'bioRxiv digest' for the App Name and enter it
5. Google will give you an app password, copy this and keep it somewhere safe

### Activating the Digest

In your GitHub repository, go to Settings -> Security -> Secrets and variables -> Actions

To get the digest running, setup the following secrets by clicking "New repository secret":

- SMTP_USER: the email you set SMTP with
- SMTP_PASSWORD: the app password Google gave to use SMTP
- GEMINI_API_KEY: the API key from Google AI Studio

Optional settings:

- EMAIL_TO: the email address you want to receive the digest in
- EMAIL_CC: if you want to copy anyone into the digests
- EMAIL_BCC: if you want to blind copy anyone into the digests
- DIGEST_INTERESTS: write your interests and the AI will give papers that follow them
- LOOKBACK_DAYS: how many days of bioRxiv papers do you want to cover (setting to more than 1 may lead to repeats in the digest)
- MAX_PAPERS_FOR_AI: the number of papers Gemini will receive for filtering (setting this number too high may result in the model exceeding context limits)
- SMTP_HOST: if you want to setup a different hosting service
- SMTP_PORT: port for the different hosting service

Congratulations, you should be receiving an email digest every day at 8am ET. Let me know if you use this by giving a star!

If you feel that something is lacking, feel free to make open an issue or make a PR to the main repository.