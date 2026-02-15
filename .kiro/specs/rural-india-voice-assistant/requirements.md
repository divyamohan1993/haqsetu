# Requirements Document: HaqSetu (हक़सेतु) — Voice-First AI Civic Assistant for Rural India

## Introduction

HaqSetu ("Bridge to Entitlements") is a voice-first, multilingual AI civic assistant engineered to connect 900+ million rural Indians with the government schemes, agricultural services, and digital public goods they are entitled to — but currently cannot access due to literacy barriers, language gaps, and connectivity constraints. The system operates across all 22 scheduled Indian languages, functions reliably on 2G networks with as low as 4.75 kbps bandwidth, maintains offline-first architecture for areas with zero or intermittent connectivity, and delivers multi-channel access via IVR telephony, WhatsApp, SMS, and USSD. HaqSetu leverages India's sovereign AI stack — Bhashini (300+ models, 100M monthly inferences), Sarvam AI (22-language API suite), and AI4Bharat (open-source IndicTrans2, IndicASR) — alongside Google Cloud and AWS infrastructure, integrating deeply with India Stack (Aadhaar, UPI, DigiLocker, ABDM) and government service delivery platforms (MyScheme, PM-KISAN, MGNREGA, PMJAY, AgMarkNet, e-NAM, CSC network). The platform is designed for full DPDPA 2023 compliance ahead of the May 2027 enforcement deadline, with Aadhaar data handling per the 2018 Puttaswamy ruling and alignment with MeitY's February 2026 AI Governance Guidelines. HaqSetu targets sub-1.5 second voice-to-voice latency, per-query costs below ₹0.85 ($0.01), and distribution through 5.34 lakh CSCs, 10,000+ FPOs, and direct-to-consumer channels, addressing the structural information asymmetry that costs eligible citizens trillions in unclaimed entitlements annually.

## Glossary

- **HaqSetu**: The platform name (Hindi: हक़सेतु); "Haq" = rights/entitlements, "Setu" = bridge
- **VoicePipeline**: The end-to-end streaming speech processing system chaining VAD → ASR → Translation → LLM Reasoning → TTS with sub-1.5s target latency
- **ASR**: Automatic Speech Recognition; primary providers are Bhashini (22 languages) and Google Cloud Speech-to-Text Chirp 3 (15+ Indian languages)
- **TTS**: Text-to-Speech synthesis; primary providers are Bhashini (22 languages), Sarvam Bulbul v3 (11 languages, 35+ voices), and Google Cloud TTS Chirp 3 HD
- **NMT**: Neural Machine Translation; powered by Bhashini NMT pipeline, AI4Bharat IndicTrans2 (462 directions, 22 languages), and Sarvam translation API
- **Bhashini**: MeitY's national language translation platform with 300+ pre-trained AI models, composable pipeline API (ASR+NMT+TTS), and ULCA open-source contribution framework
- **Sarvam_AI**: India's sovereign LLM company; provides Sarvam-1 (2B parameters, 10 Indic languages), Saaras v3 STT, Bulbul v3 TTS, and translation APIs
- **AI4Bharat**: IIT Madras research lab providing open-source IndicTrans2, IndicASR (IndicConformer), IndicVoices dataset (23,700 hours), and Indic Parler-TTS
- **India_Stack**: India's digital public infrastructure layer comprising Aadhaar (1.4B enrollments), UPI (228B annual transactions), DigiLocker (515M users), and ABDM (799M ABHA IDs)
- **MyScheme_Portal**: Government portal cataloguing 2,316+ schemes (510+ central, 1,800+ state) for eligibility matching
- **CSC**: Common Service Centre; 5.34 lakh locations (4.17 lakh rural) staffed by Village Level Entrepreneurs (VLEs), processing 33.5M transactions monthly
- **VLE**: Village Level Entrepreneur; CSC operator who earns through the 80:12:8 revenue split model
- **FPO**: Farmer Producer Organization; 10,000+ registered, connecting ~30 lakh farmers, with 4,642 integrated with e-NAM
- **DPDPA**: Digital Personal Data Protection Act 2023; rules notified November 13, 2025, full compliance mandatory by May 13, 2027; penalties up to ₹250 crore per violation
- **Consent_Manager**: DPDPA-defined digital platform for giving, managing, and withdrawing consent; must be registered with Data Protection Board by November 2026 with 7-year record retention
- **Aadhaar_Vault**: Mandatory encrypted storage for Aadhaar numbers using Hardware Security Module (HSM) key management with tokenized Reference Keys; direct storage of Aadhaar numbers is prohibited
- **VAD**: Voice Activity Detection; Silero VAD preferred for smart turn detection over simple silence thresholds
- **RNNoise**: Open-source real-time noise suppression neural network, layered with Silero VAD for rural noise environments
- **AMR_NB**: Adaptive Multi-Rate Narrowband codec operating at 4.75–12.2 kbps; the baseline audio codec for 2G/3G telephony compatibility
- **Opus**: Audio codec used by WhatsApp voice notes at 16–24 kbps with store-and-forward delivery
- **RAG_System**: Retrieval-Augmented Generation pipeline using BGE-M3 embeddings (1024 dimensions, hybrid dense+sparse retrieval) over government scheme knowledge base
- **BGE_M3**: BAAI General Embedding Multilingual Multi-functionality Multi-granularity model; 1024 dimensions; hybrid dense+sparse retrieval for multilingual semantic search
- **HingBERT**: L3Cube Pune's Hindi-English code-mixed BERT model outperforming standard BERT on Hinglish text
- **IndicXlit**: AI4Bharat's transliteration model for normalizing script variations before embedding
- **Edge_Runtime**: On-device inference runtime running 4-bit quantized models (Phi-3-mini, Gemma-2B, Vosk for offline ASR) within a 2GB local cache envelope
- **QueryOrchestrator**: Component that classifies intent, orchestrates RAG retrieval, LLM reasoning, eligibility matching, and response generation
- **SessionManager**: Component managing multi-turn conversation state, cross-channel session continuity, and user preference tracking
- **PrivacyController**: Component enforcing DPDPA compliance including consent management, data anonymization, encryption, Aadhaar vault compliance, breach notification, and audit logging
- **ChannelGateway**: Unified ingress layer normalizing inputs from IVR (AWS Connect), WhatsApp Business API, SMS gateways, and USSD into the VoicePipeline
- **SchemeGraph**: Structured knowledge graph of 2,316+ government schemes with eligibility rules, document requirements, application workflows, and inter-scheme dependency mapping
- **AgMarkNet**: Government portal tracking 300+ commodities across 4,549 markets with 2M+ monthly price records
- **e_NAM**: National Agriculture Market integrating 1,522 mandis with 17.9M registered farmers
- **PMJAY**: Pradhan Mantri Jan Arogya Yojana (Ayushman Bharat) covering ~50 crore beneficiaries with ₹5 lakh annual health cover
- **PM_KISAN**: Pradhan Mantri Kisan Samman Nidhi providing ₹6,000/year income support to 11+ crore farmer families
- **MGNREGA**: Mahatma Gandhi National Rural Employment Guarantee Act providing 100 days guaranteed employment
- **GeM**: Government e-Marketplace; crossed ₹5 lakh crore GMV in FY 2024–25; the procurement channel for B2G contracts
- **DBT**: Direct Benefit Transfer ecosystem covering 176 crore beneficiaries, having transferred ₹44 lakh crore cumulatively
- **DTMF**: Dual-Tone Multi-Frequency signaling for IVR menu navigation on feature phones
- **BharatNet**: Government program connecting gram panchayats with optical fibre; 2.18 lakh gram panchayats connected with 6.92 lakh km of fibre
- **PMGDISHA**: Pradhan Mantri Gramin Digital Saksharta Abhiyan; trained 6.39 crore, certified 4.77 crore before concluding March 2024
- **Puttaswamy_Ruling**: Supreme Court of India 2018 judgment (KS Puttaswamy v. Union of India) upholding Aadhaar for welfare while mandating strict data handling including 6-month authentication log retention limit

## Requirements

### Requirement 1: Streaming Voice Interaction Pipeline

**User Story:** As a rural farmer who cannot read or write, I want to speak naturally in my mother tongue and hear a spoken response within a couple of seconds, so that I can learn about government schemes and agricultural services without needing literacy or someone else's help.

#### Acceptance Criteria

1. WHEN a user speaks in any of the 22 scheduled Indian languages, THE VoicePipeline SHALL initiate streaming ASR processing with audio chunks ≤50ms, using Bhashini ASR as primary provider and Google Cloud Speech-to-Text Chirp 3 as fallback
2. WHEN ASR produces partial transcription hypotheses via streaming, THE VoicePipeline SHALL begin parallel LLM reasoning before the full sentence completes, achieving sub-1.5 second end-to-end voice-to-voice latency for 95% of queries and sub-800ms for 50% of queries
3. WHEN the user's detected language differs from the LLM's processing language (English), THE VoicePipeline SHALL translate input using Bhashini NMT or AI4Bharat IndicTrans2 and translate the LLM response back to the user's language before TTS synthesis
4. WHEN audio input is received from a rural environment, THE VoicePipeline SHALL apply a preprocessing chain of Silero VAD + RNNoise noise suppression before passing audio to ASR, leveraging AMR codec's built-in VAD as an additional layer for 2G/3G telephony channels
5. WHEN network bandwidth is detected below 12.2 kbps (typical 2G GPRS), THE VoicePipeline SHALL encode/decode audio using AMR-NB codec at 4.75 kbps minimum; WHEN bandwidth is 16+ kbps (EDGE or above), THE VoicePipeline SHALL use Opus at 16 kbps for improved quality
6. WHEN generating spoken responses, THE VoicePipeline SHALL use sentence-level streaming to TTS — beginning synthesis of the first sentence while the LLM is still generating subsequent sentences — using Bhashini TTS, Sarvam Bulbul v3, or Google Cloud TTS Chirp 3 HD as providers
7. WHEN processing voice input, THE VoicePipeline SHALL maintain persistent WebSocket connections to avoid connection setup overhead, and SHALL use smart turn detection via Silero VAD rather than fixed silence thresholds
8. IF ASR confidence score is below 0.7, THEN THE VoicePipeline SHALL request clarification from the user in their detected language using a natural-sounding prompt (not robotic error messages)
9. WHEN the VoicePipeline latency budget is allocated, IT SHALL conform to: network/telephony overhead (100–200ms) + VAD+noise reduction (20–50ms) + streaming ASR (200–300ms) + language detection (10–20ms) + translation (100–200ms) + RAG retrieval (50–100ms) + LLM first token (150–300ms) + TTS first byte (100–200ms)

### Requirement 2: Comprehensive Multilingual and Dialect Support

**User Story:** As a Santali-speaking tribal woman in Jharkhand, I want to interact with the system in my own language — including regional dialect and code-mixed speech — so that I am not excluded from accessing my entitlements because I don't speak Hindi or English.

#### Acceptance Criteria

1. THE VoicePipeline SHALL support ASR input in all 22 scheduled Indian languages: Assamese, Bengali, Bodo, Dogri, Gujarati, Hindi, Kannada, Kashmiri, Konkani, Maithili, Malayalam, Manipuri (Meitei), Marathi, Nepali, Odia, Punjabi, Sanskrit, Santali, Sindhi, Tamil, Telugu, and Urdu
2. THE VoicePipeline SHALL support TTS output in all 22 scheduled Indian languages, selecting voice profiles optimized for clarity and naturalness using Sarvam Bulbul v3 (35+ voices in 11 languages), Bhashini TTS, or Google Cloud TTS as available per language
3. WHEN processing code-mixed input (e.g., Hinglish), THE VoicePipeline SHALL apply HingBERT-based processing for Hindi-English and SHALL use AI4Bharat IndicXlit transliteration to normalize script variations before embedding generation
4. WHEN a user switches language mid-conversation, THE VoicePipeline SHALL detect the language change within the current utterance using Bhashini's language detection or Sarvam AI's language identification and adapt ASR, NMT, and TTS accordingly without interrupting the conversation
5. THE VoicePipeline SHALL maintain ASR Word Error Rate (WER) below 15% for the top 10 languages by user volume (Hindi, Bengali, Tamil, Telugu, Marathi, Gujarati, Kannada, Malayalam, Odia, Punjabi) and below 25% WER for remaining scheduled languages, benchmarked against AI4Bharat IndicVoices test sets
6. THE VoicePipeline SHALL use language-specific acoustic models trained on rural speech patterns, agricultural terminology, and government scheme vocabulary — leveraging the AI4Bharat IndicVoices dataset (23,700 hours, 51,000 speakers, 400+ districts) as training foundation
7. WHEN ASR accuracy for a specific language falls below acceptable thresholds in production, THE VoicePipeline SHALL route to the next-best provider in the fallback chain: Bhashini → Sarvam Saaras v3 → Google Cloud STT → offline Vosk/whisper.cpp

### Requirement 3: Government Scheme Discovery, Eligibility Matching, and Application Guidance

**User Story:** As a marginal farmer's wife with two school-age children, I want to describe my family's situation in plain language and instantly learn which schemes we qualify for — including exactly what documents I need and how to apply — so that I don't lose benefits because I didn't know about them or couldn't navigate the paperwork.

#### Acceptance Criteria

1. WHEN a user asks about government schemes, THE QueryOrchestrator SHALL search across 2,316+ schemes indexed from MyScheme Portal (510+ central, 1,800+ state-level), maintained via daily automated scraping and structured ingestion pipeline
2. WHEN a user describes their situation in natural language (e.g., "My husband is a farmer, I have two kids in school, we earn about 2 lakh a year"), THE QueryOrchestrator SHALL extract demographic attributes — age, gender, occupation, income, location (state/district/block), social category (SC/ST/OBC/General), land ownership, family composition — and match against the SchemeGraph eligibility rules
3. WHEN presenting matched schemes, THE QueryOrchestrator SHALL provide for each scheme: name (in user's language), benefits summary, eligibility criteria, required documents (with indication of which are available via DigiLocker), application process (online/offline/CSC), nearest application point, helpline number, and current scheme status (open/closed/upcoming)
4. WHEN a user requests application assistance for a specific scheme, THE QueryOrchestrator SHALL provide step-by-step spoken guidance in the user's language, including: required documents checklist, nearest CSC location, expected processing time, and common rejection reasons to avoid
5. WHEN scheme data is updated on MyScheme Portal or underlying ministry portals, THE RAG_System SHALL refresh its knowledge base within 24 hours via automated ingestion pipeline, with critical scheme changes (deadlines, eligibility modifications) reflected within 4 hours
6. THE QueryOrchestrator SHALL integrate with domain-specific data sources for specialized queries: AgMarkNet (300+ commodities, 4,549 markets) for mandi prices, e-NAM (1,522 mandis) for market access, Soil Health Cards (25+ crore issued) for soil data, PM-KISAN for installment status, MGNREGA/NREGASoft for job card and wage payment status, and PMJAY for hospital empanelment and claim status
7. WHEN multiple schemes match user criteria, THE QueryOrchestrator SHALL rank results by: (a) benefit-to-effort ratio for the specific user, (b) application deadline proximity, (c) scheme popularity and success rate in the user's district, and (d) inter-scheme synergies (e.g., recommending PM-KISAN alongside crop insurance), presenting top 5 with option to explore more
8. WHEN a user asks about the status of a previously applied scheme or DBT payment, THE QueryOrchestrator SHALL attempt real-time status lookup via available government APIs (PM-KISAN API, MGNREGA NREGASoft, PFMS) where integration exists, and provide guidance on manual status checking where API access is unavailable
9. THE SchemeGraph SHALL maintain inter-scheme dependency mapping so that when a user is eligible for Scheme A that requires Aadhaar-seeded bank account, HaqSetu proactively informs them about the Jan Dhan Yojana as a prerequisite

### Requirement 4: Low-Bandwidth and 2G Network Optimization

**User Story:** As a daily wage laborer accessing HaqSetu from a remote village with only 2G GPRS coverage, I want the system to work smoothly on my slow connection without long silences or dropped calls, so that I can actually complete my query without frustration.

#### Acceptance Criteria

1. WHEN network bandwidth is between 4.75–12.2 kbps (2G GPRS), THE VoicePipeline SHALL successfully transmit and receive voice audio using AMR-NB codec, maintaining intelligible conversation quality at the lowest 4.75 kbps rate
2. WHEN transmitting any data over constrained networks, THE VoicePipeline SHALL apply protocol-level compression (gzip/brotli for HTTP payloads, binary serialization for WebSocket frames) and SHALL minimize metadata overhead to keep per-turn payload below 5KB for non-audio data
3. WHEN network round-trip latency exceeds 500ms, THE VoicePipeline SHALL provide audio feedback to the user (e.g., a brief tone or "processing your question" spoken prompt) to prevent perceived system hang
4. THE VoicePipeline SHALL limit audio streaming to 8kHz/16-bit sample rate for all 2G-origin channels, downsampling higher-quality sources as needed before transmission
5. WHEN network connection drops mid-conversation, THE VoicePipeline SHALL implement retry with exponential backoff (1s → 2s → 4s → 8s → 16s max), and SHALL persist the conversation state server-side so the user can resume within 24 hours by calling back or re-initiating contact
6. THE VoicePipeline SHALL maintain a multi-tier cache: (a) CDN-edge cache for static scheme content, (b) regional Redis cache for frequently accessed scheme data per state, (c) device-local cache for top 500 schemes per district, reducing redundant network requests by ≥70% for repeat query patterns
7. WHEN total voice-to-voice latency exceeds 3 seconds for any query, THE VoicePipeline SHALL log detailed latency breakdown (per pipeline stage) with correlation ID for performance optimization analysis
8. WHEN operating on EDGE networks (2.5G, ~100–400 kbps), THE VoicePipeline SHALL opportunistically prefetch likely follow-up scheme data based on conversation context to reduce subsequent response latency
9. THE VoicePipeline SHALL implement connection-quality-adaptive behavior: on 2G, disable supplementary features (e.g., proactive recommendations) and focus on core query-response; on 3G+, enable richer interactions including multi-scheme comparison and document guidance

### Requirement 5: Offline-First Architecture and Edge Deployment

**User Story:** As a CSC operator in a tribal area where internet drops out multiple times a day, I want to continue helping villagers find schemes and check eligibility even when the internet is completely down, with everything syncing automatically when connectivity returns.

#### Acceptance Criteria

1. WHEN network connectivity is completely unavailable, THE Edge_Runtime SHALL process voice queries end-to-end using locally deployed models: offline ASR via Vosk (Hindi) or whisper.cpp, 4-bit quantized LLM (Phi-3-mini-4bit or Gemma-2B-4bit), and lightweight TTS
2. THE Edge_Runtime SHALL run 4-bit quantized models using GPTQ or AWQ quantization achieving ≤75% size reduction with <2% accuracy degradation, fitting within the 2GB total local cache envelope on resource-constrained ARM devices common at CSC kiosks
3. WHEN operating offline, THE Edge_Runtime SHALL provide access to pre-loaded scheme data including: (a) full scheme details for the top 500 most-accessed schemes in the user's district, (b) eligibility rules for all 2,316+ schemes in compressed decision-tree format, (c) pre-computed embeddings for offline semantic search via FAISS
4. WHEN connectivity is restored, THE Edge_Runtime SHALL execute differential sync: upload queued conversation logs and user interactions, download updated scheme data and model patches, prioritizing user-specific data first then popular schemes, using idempotent server endpoints to prevent duplication
5. THE Edge_Runtime SHALL pre-load district-specific scheme data requiring less than 5MB compressed, with monthly version-based cache invalidation and daily delta updates when connected
6. WHEN offline model confidence score is below 0.6 for any query, THE Edge_Runtime SHALL: (a) provide the best available answer with a spoken disclaimer that it may be incomplete, (b) queue the query with full context for cloud re-processing when connectivity returns, and (c) proactively notify the user of the improved answer upon next interaction
7. THE Edge_Runtime SHALL maintain total local storage below 2GB, allocated as: quantized ASR model (~250MB), quantized LLM (~1.2GB), TTS model (~150MB), scheme data + embeddings (~300MB), conversation queue + logs (~100MB)
8. THE Edge_Runtime SHALL implement a durable command queue that persists all user actions to local SQLite storage and replays them sequentially against cloud endpoints when connectivity returns, with conflict resolution following cloud-data-takes-precedence policy
9. WHEN the Edge_Runtime detects connectivity after a period of offline operation, IT SHALL perform sync in priority order: (a) upload pending user-initiated transactions, (b) download critical scheme updates (deadline changes, new high-priority schemes), (c) sync conversation logs, (d) pull model updates if available

### Requirement 6: Multi-Channel Delivery (IVR, WhatsApp, SMS, USSD)

**User Story:** As a feature phone user without a data plan, I want to call a toll-free number and navigate with number keys to find my schemes, while my son can use WhatsApp on his smartphone for the same service — so that every family member can access HaqSetu regardless of their device or connectivity.

#### Acceptance Criteria

1. THE ChannelGateway SHALL support voice interactions through IVR telephony, integrating with Amazon Connect for Indian PSTN at $0.0022/minute inbound/outbound, with DTMF keypad navigation for feature phone users and full voice interaction for smartphone callers
2. THE ChannelGateway SHALL support text and voice interactions through WhatsApp Business API, handling both typed text messages and voice notes (Opus codec, 16–24 kbps), with template message support for proactive scheme notifications and conversation threading
3. THE ChannelGateway SHALL support text-only interactions through SMS gateways, with Unicode support for all Indic scripts, concatenated SMS handling for responses exceeding 160 characters, and compliance with TRAI DND/preference regulations
4. THE ChannelGateway SHALL support USSD-based interactions for feature phones without data connectivity, structured as menu-driven navigation within the 182-character per-screen USSD protocol limit, with session timeout handling and position-aware state management
5. THE ChannelGateway SHALL support a missed-call-back model (following Gram Vaani's Mobile Vaani pattern serving 3M+ users) where users give a missed call to a designated number and receive a callback with the IVR session initiated, providing zero-cost access for users who cannot afford outbound calls
6. WHEN a user initiates contact through any channel, THE SessionManager SHALL create a unified session and maintain full conversation context within that channel across multiple interactions
7. WHEN a user switches between channels (e.g., starts on IVR, continues on WhatsApp), THE SessionManager SHALL allow conversation resumption using phone number matching or a spoken/typed session code, preserving all prior context including extracted demographics and scheme matches
8. WHEN using WhatsApp, THE VoicePipeline SHALL support: text messages (with auto-language-detection), voice notes (processed through full ASR pipeline), document/image sharing (for uploading required scheme documents), and location sharing (for nearest CSC/office lookup)
9. WHEN using IVR, THE VoicePipeline SHALL present a language selection menu within the first 10 seconds, support DTMF navigation for all core workflows (scheme search, eligibility check, status inquiry), and provide voice-guided navigation for non-DTMF interactions
10. THE ChannelGateway SHALL normalize all channel-specific inputs into a unified `HaqSetuRequest` format before passing to the VoicePipeline, ensuring channel-agnostic processing logic

### Requirement 7: DPDPA 2023 Compliance and Data Privacy

**User Story:** As a rural user sharing my family details and Aadhaar information with HaqSetu, I want to know exactly what data is collected, have control over how it's used, and be confident it's protected by law — so that I can trust the system with my personal information.

#### Acceptance Criteria

1. THE PrivacyController SHALL obtain free, specific, informed consent before collecting any personal data, delivered as a spoken consent prompt in the user's language (not pre-ticked defaults); consent SHALL be granular per purpose: scheme matching, Aadhaar authentication, analytics, proactive notifications
2. THE PrivacyController SHALL provide privacy notices in clear language available in all 22 Eighth Schedule languages, as mandated by DPDPA 2023, explaining what data is collected, for what purpose, and how long it will be retained
3. THE PrivacyController SHALL allow users to withdraw consent and request deletion of their personal data, completing deletion within 30 days of request — with voice-accessible withdrawal flow (not just web forms)
4. THE PrivacyController SHALL encrypt all personal data at rest using AES-256-GCM with keys managed via HSM-backed key management (AWS KMS or Google Cloud KMS, India regions only)
5. THE PrivacyController SHALL encrypt all data in transit using TLS 1.3, with certificate pinning for critical integrations (India Stack APIs, payment gateways)
6. WHEN storing conversation logs, THE PrivacyController SHALL anonymize all PII (phone numbers, Aadhaar references, names, addresses) using irreversible hashing before persistence, retaining only anonymized transcripts for quality improvement
7. THE PrivacyController SHALL implement data minimization: collect only the minimum data necessary for the requested service, delete intermediate processing artifacts (raw audio, unencrypted transcripts) within 24 hours of query completion, and retain user data only for the minimum period required — with conversation logs retained maximum 90 days and audit logs retained minimum 1 year per DPDPA
8. THE PrivacyController SHALL provide users access to their stored data upon request within 7 days, delivered in the user's language via their preferred channel (voice summary or text document)
9. WHEN processing sensitive data categories (health records via ABDM, caste/category information, biometric references), THE PrivacyController SHALL apply enhanced consent requirements per DPDPA 2023 Section 9, with explicit re-confirmation before each access
10. THE PrivacyController SHALL implement data localization by ensuring all Indian user data is stored and processed exclusively within India, using only the Mumbai (ap-south-1/asia-south1) and Hyderabad/Delhi regions of AWS/GCP
11. THE PrivacyController SHALL maintain comprehensive audit logs of all data access and processing activities, retained for minimum 1 year, including: who accessed what data, when, for what purpose, and from which system component
12. THE PrivacyController SHALL implement breach notification capability to report incidents to the Data Protection Board within 72 hours and to affected users without unreasonable delay, as mandated by DPDPA
13. WHEN processing data for government subsidy/benefit/service delivery, THE PrivacyController SHALL classify such processing as "legitimate use" under DPDPA Section 5 where applicable, while still maintaining all security safeguards and user transparency obligations
14. THE PrivacyController SHALL support Consent Manager registration requirements per DPDPA Rules (effective November 2026), maintaining consent records for a minimum of 7 years

### Requirement 8: Aadhaar-Compliant India Stack Integration

**User Story:** As a rural beneficiary, I want to verify my identity using my Aadhaar OTP and pull my documents from DigiLocker through a simple voice conversation, so that I can complete scheme applications without visiting multiple offices or carrying paper documents.

#### Acceptance Criteria

1. WHERE Aadhaar authentication is required, THE VoicePipeline SHALL integrate with UIDAI eKYC API supporting OTP-based verification, with Virtual ID support for privacy-preserving authentication that does not reveal the actual 12-digit Aadhaar number
2. THE VoicePipeline SHALL NEVER store Aadhaar numbers directly; all Aadhaar data SHALL be stored as tokenized Reference Keys in an encrypted Aadhaar Data Vault with HSM key management, in strict compliance with the Puttaswamy ruling and UIDAI circular requirements
3. THE VoicePipeline SHALL retain Aadhaar authentication transaction logs for a maximum of 6 months only, per the Supreme Court's directive in Puttaswamy (reduced from the earlier 5-year period)
4. WHERE payment processing is required (scheme fees, CSC service charges), THE VoicePipeline SHALL integrate with UPI APIs, supporting UPI 3.0 conversational voice payments and UPI Lite for offline transactions up to ₹500
5. WHERE document verification is needed, THE VoicePipeline SHALL integrate with DigiLocker Pull API for consent-based retrieval of Aadhaar, PAN, certificates, caste certificates, income certificates, and land records where available (943 crore+ documents ecosystem)
6. WHERE health-related queries arise, THE VoicePipeline SHALL integrate with ABDM Health Information Exchange using ABHA ID for consent-based health record access, enabling PMJAY eligibility verification and hospital empanelment lookup
7. WHEN authenticating via Aadhaar OTP, THE VoicePipeline SHALL support voice-guided OTP entry with retry up to 3 attempts, and SHALL offer alternative authentication (phone OTP, QR-code based verification as per November 2025 UIDAI system) when biometric or primary OTP fails
8. WHEN fingerprint authentication failure occurs (6–12% failure rate among manual laborers), THE VoicePipeline SHALL proactively suggest OTP-based or face authentication alternatives rather than repeating failed biometric attempts
9. WHEN any India Stack API call is made, THE VoicePipeline SHALL comply with published rate limits, implement circuit-breaker patterns for API degradation, use exponential backoff for transient failures, and log all API interactions for audit compliance
10. WHEN UPI transactions are initiated or completed, THE VoicePipeline SHALL provide real-time transaction confirmation in the user's language via both voice (on active call/session) and SMS (as persistent record), including transaction ID, amount, and payee details

### Requirement 9: Agricultural Data Services

**User Story:** As a smallholder farmer, I want to ask HaqSetu about today's mandi prices for my crops, check my soil health card results, know the weather forecast, and find out my PM-KISAN installment status — all in one voice conversation in my language.

#### Acceptance Criteria

1. WHEN a farmer asks about commodity prices, THE QueryOrchestrator SHALL retrieve current market prices from AgMarkNet data (300+ commodities, 2,000+ varieties, 4,549 markets), presenting prices from the nearest 3 mandis to the farmer's location with comparison to district and state averages
2. WHEN a farmer asks about market access, THE QueryOrchestrator SHALL provide e-NAM integration data including nearby participating mandis (out of 1,522 integrated), current lot prices, and guidance on e-NAM registration for unregistered farmers
3. WHEN a farmer asks about soil health, THE QueryOrchestrator SHALL integrate with the Soil Health Card portal to retrieve test results linked to the farmer's plot (using registered mobile number or card number), explaining results and fertilizer recommendations in the farmer's language using simplified agricultural terminology
4. WHEN a farmer asks about PM-KISAN status, THE QueryOrchestrator SHALL check installment disbursement status (21 installments, ₹4.09 lakh crore disbursed) using the farmer's registered Aadhaar-seeded account details, and SHALL guide farmers through face authentication requirements on the PM-KISAN mobile app if needed
5. WHEN a farmer asks about weather, THE QueryOrchestrator SHALL provide IMD weather data for the farmer's district; since IMD lacks a public API (requires IP whitelisting), THE system SHALL maintain a curated weather data pipeline using authorized access or third-party weather APIs (OpenWeatherMap, Visual Crossing) with India-specific agricultural weather alerts
6. WHEN a farmer asks about crop insurance, THE QueryOrchestrator SHALL provide PMFBY (Pradhan Mantri Fasal Bima Yojana) information including coverage details, claim process, premium calculation for the farmer's crop and district, and claim status lookup
7. THE QueryOrchestrator SHALL integrate with ISRO Bhuvan satellite data APIs (WMS/WMTS) for crop monitoring, NDVI-based crop health assessment, and agricultural drought advisory when farmers report crop distress
8. WHEN a farmer references the Kisan Call Centre (1800-180-1551), THE QueryOrchestrator SHALL provide equivalent or superior information with 24/7 availability (versus KCC's 6 AM–10 PM limitation) in all 22 languages (versus KCC's operational subset)

### Requirement 10: Performance, Scalability, and Cost Efficiency

**User Story:** As the HaqSetu platform operator, I want the system to handle surges during scheme enrollment periods (PM-KISAN installment dates, harvest season mandi queries) while keeping per-query costs below ₹1, so that the service remains financially sustainable at national scale.

#### Acceptance Criteria

1. THE VoicePipeline SHALL achieve sub-1.5 second voice-to-voice latency (p95) and sub-800ms (p50) for cloud-connected users, measured end-to-end from user speech completion to first TTS audio byte received
2. THE VoicePipeline SHALL support at least 10,000 concurrent voice sessions per deployment region (Mumbai, Hyderabad/Delhi), with horizontal auto-scaling to 50,000 concurrent sessions during peak enrollment periods
3. WHEN query volume exceeds 80% of provisioned capacity, THE VoicePipeline SHALL trigger auto-scaling within 2 minutes, adding capacity in pre-warmed increments to avoid cold-start latency spikes
4. THE VoicePipeline SHALL maintain 99.5% uptime (measured monthly, excluding planned maintenance windows announced 48 hours in advance), with no more than 22 minutes of unplanned downtime per month
5. THE VoicePipeline SHALL maintain all-inclusive per-query cost below ₹0.85 ($0.01) at steady-state volume of 1M+ queries/day, computed across: ASR (Sarvam Saaras v3 at ₹30/hour or Google STT at $0.016/min), LLM (Gemini 2.5 Flash at $0.15/$0.60 per million tokens or Amazon Nova Pro at $0.0008/$0.0032 per 1K tokens), TTS (Sarvam Bulbul v3 at ₹15/10K chars), translation (Sarvam at ₹20/10K chars), and infrastructure
6. THE RAG_System SHALL retrieve relevant scheme information with latency under 100ms (p50) and under 200ms (p95) from the Qdrant vector database
7. THE VoicePipeline SHALL process batch updates of scheme data (daily ingestion from MyScheme, AgMarkNet, e-NAM) without service interruption, using blue-green index deployment for the RAG_System
8. THE VoicePipeline SHALL implement intelligent provider routing: use Sarvam AI for supported languages (lower cost, India-optimized), fall back to Google Cloud for broader coverage, and use Amazon Nova Pro/Gemini Flash dynamically based on real-time cost and latency metrics
9. THE VoicePipeline SHALL implement aggressive caching: common translation pairs (TTL: 30 days), popular scheme summaries (TTL: 7 days), user session data (TTL: 30 min active / 24 hours resumable), targeting ≥70% cache hit rate for repeat patterns

### Requirement 11: CSC and FPO Distribution Network

**User Story:** As a Village Level Entrepreneur (VLE) at a CSC, I want a dashboard showing how many villagers I've helped, the schemes they've applied for, and my earned commission, so that I can track my income and identify villagers who might benefit from proactive outreach.

#### Acceptance Criteria

1. THE VoicePipeline SHALL be deployable across 5.34 lakh CSC locations (4.17 lakh rural), with CSC-optimized deployment supporting both online (cloud-connected) and offline (Edge_Runtime) operation modes
2. THE VoicePipeline SHALL support direct-to-consumer access via WhatsApp (India's most used messaging app), IVR toll-free number, USSD, and SMS for users who cannot physically visit a CSC
3. WHERE CSC-mediated access is used, THE VoicePipeline SHALL log all transactions with CSC ID and VLE identifier, computing revenue shares per the 80:12:8 model (80% VLE, 12% state agency, 8% CSC SPV) or applicable state-specific split
4. THE VoicePipeline SHALL support deployment and outreach through 10,000+ FPOs, integrating with e-NAM (4,642 FPO-integrated mandis) for agricultural market access and providing FPO-specific scheme discovery for agricultural cooperatives
5. THE VoicePipeline SHALL provide CSC operators with a web-based dashboard showing: daily/weekly/monthly usage statistics, scheme application success rates, revenue earned, top queried schemes in their area, and anonymized user satisfaction metrics
6. THE VoicePipeline SHALL provide FPO leaders with an agricultural-focused dashboard showing: member farmer activity, commodity price trends from nearby mandis, collective scheme eligibility, and aggregated soil health data
7. THE VoicePipeline SHALL support procurement listing on the GeM portal for B2G contracts, with pricing models including per-transaction, SaaS subscription, and project-based consulting, aligning with government procurement norms and cumulative savings tracking

### Requirement 12: Multilingual Semantic Search and RAG Pipeline

**User Story:** As a user who doesn't know the official name of schemes, I want to describe what I need in everyday language (like "help for buying a cow" or "money for daughter's wedding") and get relevant scheme matches, so that I don't need to know bureaucratic terminology.

#### Acceptance Criteria

1. THE RAG_System SHALL use BGE-M3 embeddings (1024 dimensions, hybrid dense+sparse retrieval) as the primary multilingual embedding model, with optional language-specific fine-tuning for high-traffic languages using domain-specific agricultural and civic corpora
2. WHEN indexing scheme documents, THE RAG_System SHALL create language-specific embeddings for all 22 languages, with separate Qdrant collections per language for optimized retrieval, and cross-lingual query support via translate-then-search for lower-resource languages
3. WHEN retrieving information, THE RAG_System SHALL use hybrid search combining vector similarity (HNSW index, M=16, ef_construct=100 in Qdrant) with BM25 keyword matching, applying payload-based filtering on scheme metadata (category, state, eligibility criteria)
4. THE RAG_System SHALL use domain-specific fine-tuning of embeddings on: agriculture terminology (crop names in local languages, farming practices), government scheme vocabulary (yojana/abhiyan/mission mappings), health terms (PMJAY procedure names in vernacular), and financial inclusion terms (DBT, Jan Dhan, Mudra loan colloquialisms)
5. WHEN user query is ambiguous or maps to multiple intent categories, THE RAG_System SHALL retrieve top 10 candidate documents using hybrid search, then use the cloud LLM for contextual re-ranking considering user profile, conversation history, and district-specific relevance, presenting top 5 to the user
6. THE RAG_System SHALL update vector indices incrementally when new scheme data is ingested (daily sync), without requiring full index rebuild, using Qdrant's upsert operations for modified/new schemes and soft-delete for discontinued schemes
7. WHEN semantic search confidence (maximum cosine similarity score) is below 0.5, THE RAG_System SHALL fall back to keyword-based BM25 search, and if both yield poor results, SHALL attempt query reformulation by asking the user a clarifying question
8. THE RAG_System SHALL chunk scheme documents at 256–512 tokens with paragraph-break delimiters and overlap of 50 tokens, tagging each chunk with: language, scheme_id, category, state, eligibility_summary, and last_updated metadata
9. FOR Hindi-specific queries (largest user segment), THE RAG_System SHALL optionally use DeepRAG or equivalent Hindi-specific retrieval model, which has demonstrated 23% improvement in retrieval precision over general multilingual models

### Requirement 13: Audio Quality and Rural Noise Handling

**User Story:** As a farmer calling HaqSetu while standing in my field with tractors, animals, and wind in the background, I want the system to still understand what I'm saying, so that I don't have to find a quiet room to use it.

#### Acceptance Criteria

1. WHEN audio input contains background noise above -20dB SNR (common in rural outdoor environments), THE VoicePipeline SHALL apply the Silero VAD + RNNoise preprocessing chain before passing audio to ASR, reducing noise floor by at least 15dB
2. THE VoicePipeline SHALL use Silero VAD for speech segment identification, configured with: (a) 512 sample window size for 16kHz audio, (b) speech probability threshold of 0.5, (c) minimum speech duration of 250ms, and (d) maximum silence within speech of 300ms
3. WHEN multiple speakers are detected in the audio stream, THE VoicePipeline SHALL focus on the primary speaker (highest energy, most continuous speech) and suppress secondary speakers, using speaker diarization when available
4. THE VoicePipeline SHALL maintain ASR accuracy above 80% WER in environments with up to 70dB ambient noise (equivalent to busy marketplace or tractor operation), validated through testing with noise profiles collected from actual rural Indian environments
5. WHEN audio quality is insufficient for reliable ASR (confidence consistently below 0.5 across 3 attempts), THE VoicePipeline SHALL suggest the user move to a quieter location or try SMS/USSD as alternative channels, delivered as a helpful spoken suggestion rather than a terse error message
6. THE VoicePipeline SHALL support acoustic echo cancellation for IVR scenarios where loudspeaker output may feed back into the microphone, particularly relevant for CSC kiosk deployments with shared speakers
7. THE VoicePipeline SHALL be optimized for common rural Indian ambient sounds — agricultural machinery, livestock, outdoor markets, wind, rain on tin roofs — through acoustic model fine-tuning on rural noise profiles from the AI4Bharat IndicVoices dataset (400+ districts represented)
8. WHEN receiving audio via AMR-NB codec from 2G networks, THE VoicePipeline SHALL leverage the codec's built-in Comfort Noise Generation (CNG) and Voice Activity Detection as a first-pass filter before applying the Silero VAD + RNNoise pipeline

### Requirement 14: Conversation Management and Multi-Turn Dialog

**User Story:** As an elderly user who is not familiar with technology, I want to have a natural back-and-forth conversation where the system remembers what I already said and guides me patiently, so that I feel like I'm talking to a helpful human rather than a machine.

#### Acceptance Criteria

1. THE SessionManager SHALL maintain full conversation context for up to 20 turns or 30 minutes (whichever comes first), preserving: extracted user demographics, identified scheme matches, eligibility determinations, and all conversational exchanges
2. WHEN a user refers to previous information using pronouns, colloquial references, or implicit context (e.g., "that first scheme you mentioned" or "the one for my daughter"), THE SessionManager SHALL resolve references correctly using the conversation history and extracted entity graph
3. WHEN a conversation is interrupted (call drop, network loss, user distraction), THE SessionManager SHALL persist state server-side and allow resumption within 24 hours using: (a) automatic phone number matching on same channel, (b) spoken session code for cross-channel resumption, or (c) VLE-assisted session lookup at CSC
4. THE SessionManager SHALL progressively build a user preference model within the session: tracking language comfort level (adjusting vocabulary complexity), preferred interaction speed (slower for elderly users), frequently asked topic areas, and geographic context
5. WHEN a user asks follow-up questions, THE SessionManager SHALL maintain topic continuity without requiring the user to re-state context — e.g., after discussing PM-KISAN eligibility, a follow-up "what documents do I need?" SHALL be understood as referring to PM-KISAN
6. THE SessionManager SHALL clear sensitive information (Aadhaar tokens, OTP values, UPI transaction IDs) from conversation context within 5 minutes of transaction completion, retaining only anonymized transaction references
7. WHEN session timeout approaches (at 25 minutes or 18 turns), THE SessionManager SHALL notify the user and offer to: (a) save the session for later resumption, (b) send a summary of discussed schemes via SMS/WhatsApp, or (c) connect to a CSC operator for continued assistance
8. THE SessionManager SHALL implement a guided conversation flow for first-time users: introducing HaqSetu's capabilities, collecting basic demographics through natural conversation (not form-filling), and demonstrating a sample scheme lookup — all within the first 2 minutes

### Requirement 15: Error Handling, Graceful Degradation, and Human Escalation

**User Story:** As a user who might not understand why something isn't working, I want clear, friendly guidance in my language when problems occur, and the option to speak to a real person if the system can't help me, so that I never feel stuck or abandoned.

#### Acceptance Criteria

1. WHEN ASR fails to produce usable transcription after initial attempt, THE VoicePipeline SHALL request the user to repeat slowly and clearly in simpler terms, using a warm, patient tone; after 3 consecutive failures, SHALL offer channel alternatives (e.g., "Would you like to try typing your question on WhatsApp instead?")
2. WHEN translation confidence is below 0.6, THE VoicePipeline SHALL ask clarifying questions in the user's source language, breaking the query into simpler parts, rather than proceeding with uncertain translation
3. IF any external API call fails (India Stack, MyScheme, AgMarkNet, LLM providers), THEN THE VoicePipeline SHALL execute the fallback chain: primary service → secondary service → cached data with staleness indicator → degraded service → human escalation, ensuring the user always receives some useful response
4. WHEN the LLM cannot generate a confident answer (e.g., novel scheme query, ambiguous eligibility case, system limitation), THE VoicePipeline SHALL: (a) provide the best available partial information, (b) clearly communicate what it cannot determine, and (c) offer to connect the user with the Kisan Call Centre (1800-180-1551), nearest CSC operator, or scheme-specific helpline
5. ALL error messages SHALL be delivered in the user's selected language, using natural conversational tone rather than technical error codes, and SHALL include a suggested next action
6. WHEN the system experiences high load (>80% capacity), THE VoicePipeline SHALL queue incoming requests with position indication and estimated wait time spoken to the user, while prioritizing completion of in-progress conversations over new requests
7. IF critical services are unavailable (all ASR providers down, database unreachable, LLM quota exhausted), THEN THE VoicePipeline SHALL: (a) activate emergency cached-response mode for common queries, (b) provide service status via a spoken message, (c) offer the nearest CSC contact and government helpline numbers, and (d) log the outage for operations team with auto-alert
8. THE VoicePipeline SHALL implement circuit-breaker patterns for all external dependencies with: open threshold (5 consecutive failures or 50% failure rate in 60s), half-open probe (single test request every 30s), and close threshold (3 consecutive successes)

### Requirement 16: Monitoring, Analytics, and Operational Intelligence

**User Story:** As the HaqSetu operations team, I want real-time visibility into system health, language-wise usage patterns, scheme demand by geography, and cost tracking by provider, so that I can optimize performance and demonstrate impact to government stakeholders.

#### Acceptance Criteria

1. THE VoicePipeline SHALL log every query with: correlation_id, timestamp, user language, channel type, query intent classification, schemes discussed, response time breakdown (per pipeline stage), ASR/translation confidence scores, and outcome (resolved/escalated/abandoned)
2. THE VoicePipeline SHALL track and expose via dashboards: ASR Word Error Rate per language, translation BLEU/chrF++ scores per language pair, RAG retrieval precision@5, user satisfaction (post-interaction rating where collected), and session completion rate
3. THE VoicePipeline SHALL monitor API latencies (p50/p95/p99) for all external integrations (Bhashini, Sarvam, Google Cloud, AWS, India Stack APIs, MyScheme) with real-time anomaly detection
4. THE VoicePipeline SHALL generate automated reports: daily operational summary (volume, latency, errors), weekly scheme demand analysis (top queried schemes by state/district, emerging trends), monthly impact report (users served, schemes matched, estimated DBT facilitated)
5. WHEN error rates exceed 5% across any 15-minute window, THE VoicePipeline SHALL trigger PagerDuty/equivalent alerts to the on-call operations engineer with full context (error distribution, affected languages/channels, timeline)
6. THE VoicePipeline SHALL track all-inclusive cost per query segmented by: ASR provider, LLM provider, TTS provider, translation provider, infrastructure (compute, storage, network), and telephony — enabling real-time provider cost-optimization decisions
7. ALL analytics data SHALL be anonymized before storage and visualization to comply with DPDPA requirements — no PII in dashboards, only aggregated demographic and geographic data
8. THE VoicePipeline SHALL provide stakeholder-facing dashboards showing: geographic heatmap of queries by district, language distribution, scheme demand forecasting, impact metrics (estimated unclaimed benefits surfaced, successful applications facilitated), and rural vs. semi-urban usage patterns
9. THE VoicePipeline SHALL implement A/B testing infrastructure for: ASR provider comparison, LLM prompt optimization, TTS voice preference testing, and conversation flow experimentation — enabling data-driven continuous improvement

### Requirement 17: AI Governance and Responsible AI Compliance

**User Story:** As a government partner evaluating HaqSetu for statewide deployment, I want confidence that the AI system meets MeitY's AI Governance Guidelines, doesn't discriminate against any social group, and provides transparent, explainable recommendations, so that I can approve its use for public service delivery.

#### Acceptance Criteria

1. THE VoicePipeline SHALL comply with MeitY's February 2026 AI Governance Guidelines eight principles: transparency, accountability, safety and reliability, privacy and security, fairness and non-discrimination, human-centered values, inclusive innovation, and digital-by-design governance
2. THE VoicePipeline SHALL implement bias monitoring across all 22 languages and major social categories (SC/ST/OBC/General, gender, age groups, geographic regions), tracking: response quality parity, scheme recommendation fairness, and ASR accuracy equity
3. WHEN providing scheme recommendations, THE QueryOrchestrator SHALL provide explainable reasoning — telling the user WHY they were matched to a scheme (e.g., "Based on your family income being below ₹2 lakh and your occupation as a farmer, you may be eligible for PM-KISAN") rather than opaque recommendations
4. THE VoicePipeline SHALL maintain a model card for each AI model in the pipeline documenting: training data sources, known limitations, language-specific performance metrics, and bias evaluation results
5. THE VoicePipeline SHALL support the IndiaAI Mission's Safe and Trusted AI requirements including: machine unlearning capability (for consent withdrawal), synthetic data generation disclosure, and AI bias mitigation measures
6. THE VoicePipeline SHALL classify its operations under MeitY's activity-based risk framework, with government scheme recommendations and Aadhaar-linked services treated as high-risk activities requiring enhanced audit, human oversight, and explainability
7. THE VoicePipeline SHALL maintain an AI incident log recording: model failures, hallucinated scheme information, biased recommendations, privacy incidents, and user complaints — contributing to the national AI incident database if/when operationalized
8. THE VoicePipeline SHALL implement human-in-the-loop review for high-stakes decisions: scheme rejection recommendations, Aadhaar authentication failures, and payment-related transactions, with clear escalation paths to human operators