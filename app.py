"""
ReceiptSnap AI - Snap it. Understand it. Split it.
Main Streamlit Application.
"""

import os
import re
import smtplib
from email.message import EmailMessage
from typing import Optional, Tuple

import streamlit as st
from google import genai
from google.genai import types

from prompts import (
    SYSTEM_PROMPT,
    WELCOME_MESSAGE_TEMPLATE,
    SUMMARY_REQUEST_PROMPT,
)

# ==============================================================================
# CONFIGURATION
# ==============================================================================
# The model name is configured in this single location.
MODEL_NAME = "gemini-3.8-flash"

APP_TITLE = "ReceiptSnap AI"
APP_TAGLINE = "Snap it. Understand it. Split it."

# ==============================================================================
# STREAMLIT PAGE CONFIG & STYLES
# ==============================================================================
st.set_page_config(
    page_title=f"{APP_TITLE} — {APP_TAGLINE}",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom minimal CSS for a polished, modern, trustworthy interface
st.markdown(
    """
    <style>
    /* Global Typography & Card Styling */
    .block-container {
        padding-top: 1.8rem;
        padding-bottom: 2.5rem;
        max-width: 1200px;
    }
    
    .brand-header {
        display: flex;
        align-items: center;
        gap: 14px;
        margin-bottom: 0.2rem;
    }
    
    .brand-title {
        font-size: 2.1rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        margin: 0;
        color: #1E293B;
    }
    
    .brand-tagline {
        font-size: 1.05rem;
        color: #64748B;
        font-weight: 400;
        margin-top: 0.1rem;
        margin-bottom: 1.2rem;
    }
    
    .user-badge {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        background-color: #F1F5F9;
        border: 1px solid #CBD5E1;
        border-radius: 9999px;
        padding: 4px 14px;
        font-size: 0.85rem;
        color: #334155;
        font-weight: 500;
    }
    
    .feature-card {
        background-color: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 18px 20px;
        margin-bottom: 16px;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
    }
    
    .split-metric-card {
        background-color: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 10px;
        padding: 14px;
        text-align: center;
    }
    
    .footer-note {
        font-size: 0.82rem;
        color: #94A3B8;
        text-align: center;
        margin-top: 2.5rem;
        padding-top: 1rem;
        border-top: 1px solid #E2E8F0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ==============================================================================
# HELPER FUNCTIONS: VALIDATION & MATH
# ==============================================================================
def validate_email(email: str) -> bool:
    """Validate email address using a robust standard pattern."""
    if not email or not isinstance(email, str):
        return False
    email = email.strip()
    pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
    return bool(re.match(pattern, email))


def validate_name(name: str) -> bool:
    """Validate user full name."""
    if not name or not isinstance(name, str):
        return False
    return len(name.strip()) >= 2


def calculate_equal_split(total: float, num_people: int) -> dict:
    """
    Deterministically calculate an equal bill split in pure Python.
    Prevents floating-point rounding issues and accounts for the remainder.
    """
    if num_people < 1:
        raise ValueError("Number of people must be at least 1.")
    if total < 0:
        raise ValueError("Total amount cannot be negative.")

    per_person = round(total / num_people, 2)
    remainder = round(total - (per_person * num_people), 2)
    
    return {
        "total": total,
        "num_people": num_people,
        "per_person": per_person,
        "remainder": remainder,
    }


def extract_estimated_total(text: str) -> Optional[float]:
    """
    Best-effort regex extraction of the grand total from Gemini's response
    to auto-populate the Bill Splitter total field.
    """
    if not text:
        return None
    patterns = [
        r"(?:Grand\s*Total|Final\s*Total|Total)[\s:=—–\-]*[₹$€£]?\s*([0-9][0-9,]*\.?[0-9]{0,2})",
        r"[₹$€£]\s*([0-9][0-9,]*\.[0-9]{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            clean_str = match.group(1).replace(",", "")
            try:
                val = float(clean_str)
                if val > 0:
                    return val
            except ValueError:
                continue
    return None

# ==============================================================================
# GEMINI CLIENT & CHAT FUNCTIONS
# ==============================================================================
@st.cache_resource
def get_gemini_client(api_key: str) -> Optional[genai.Client]:
    """
    Initialize and cache the Gemini client.
    Cached so it is not re-instantiated on every Streamlit rerun.
    """
    if not api_key or not api_key.strip():
        return None
    try:
        return genai.Client(api_key=api_key.strip())
    except Exception as e:
        st.error(f"Error initializing Google GenAI Client: {e}")
        return None


def get_configured_api_key() -> str:
    """Fetch Gemini API Key from Streamlit secrets or environment."""
    key = ""
    if "GEMINI_API_KEY" in st.secrets:
        key = st.secrets["GEMINI_API_KEY"]
    elif "GOOGLE_API_KEY" in st.secrets:
        key = st.secrets["GOOGLE_API_KEY"]
    elif "GEMINI_API_KEY" in os.environ:
        key = os.environ["GEMINI_API_KEY"]
    elif "GOOGLE_API_KEY" in os.environ:
        key = os.environ["GOOGLE_API_KEY"]
    return key.strip() if key else ""


def create_gemini_chat(client: genai.Client):
    """Create a persistent multi-turn chat session with SYSTEM_PROMPT."""
    try:
        return client.chats.create(
            model=MODEL_NAME,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.2,
            ),
        )
    except Exception as e:
        st.error(f"Failed to create chat session with model '{MODEL_NAME}': {e}")
        return None


def get_safe_response_text(response) -> str:
    """Safely extract plain text from Gemini response object."""
    if not response:
        return ""
    try:
        if hasattr(response, "text") and response.text:
            return response.text
        # Fallback check for candidate parts
        if hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, "content") and candidate.content and candidate.content.parts:
                parts_text = [
                    p.text for p in candidate.content.parts if hasattr(p, "text") and p.text
                ]
                if parts_text:
                    return "\n".join(parts_text)
    except Exception:
        pass
    return "Unable to extract response from Gemini."

# ==============================================================================
# EMAIL SMTP ACTION
# ==============================================================================
def send_email_smtp(
    recipient: str,
    subject: str,
    body: str,
    gmail_user: str,
    gmail_password: str,
) -> Tuple[bool, str]:
    """
    Send receipt summary email via Gmail SMTP using Python's standard library
    smtplib and email.message.EmailMessage.
    
    Security: Credentials are never logged, printed, or exposed in the UI.
    """
    if not gmail_user or not gmail_password:
        return False, "Gmail credentials not configured in secrets.toml."
    
    if not validate_email(recipient):
        return False, "Recipient email address is invalid."

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"ReceiptSnap AI <{gmail_user}>"
        msg["To"] = recipient
        msg.set_content(body)

        # Connect via SMTP_SSL on port 465
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
            server.login(gmail_user, gmail_password)
            server.send_message(msg)

        return True, "Email sent successfully!"
    except smtplib.SMTPAuthenticationError:
        return (
            False,
            "SMTP Authentication failed. Please verify your Gmail Address and App Password.",
        )
    except smtplib.SMTPConnectError:
        return False, "Failed to connect to Gmail SMTP server. Check internet connection."
    except Exception:
        return (
            False,
            "Unable to send the email right now. Please check your Gmail configuration.",
        )

# ==============================================================================
# SESSION STATE MANAGEMENT
# ==============================================================================
def initialize_session_state():
    """Initialize all Streamlit session state keys."""
    if "onboarded" not in st.session_state:
        st.session_state.onboarded = False
    if "user_name" not in st.session_state:
        st.session_state.user_name = ""
    if "user_email" not in st.session_state:
        st.session_state.user_email = ""
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "chat" not in st.session_state:
        st.session_state.chat = None
    if "receipt_uploaded" not in st.session_state:
        st.session_state.receipt_uploaded = False
    if "active_receipt_name" not in st.session_state:
        st.session_state.active_receipt_name = None
    if "active_receipt_bytes" not in st.session_state:
        st.session_state.active_receipt_bytes = None
    if "active_receipt_mime" not in st.session_state:
        st.session_state.active_receipt_mime = None
    if "detected_total" not in st.session_state:
        st.session_state.detected_total = 0.0
    if "latest_summary" not in st.session_state:
        st.session_state.latest_summary = None


def add_message(
    role: str,
    content: str,
    image_bytes: Optional[bytes] = None,
    mime_type: Optional[str] = None,
):
    """Add a structured message to session history."""
    st.session_state.messages.append(
        {
            "role": role,
            "content": content,
            "image_bytes": image_bytes,
            "mime_type": mime_type,
        }
    )


def render_message(msg: dict):
    """Render a single chat message safely in the UI."""
    avatar = "👤" if msg["role"] == "user" else "🧾"
    with st.chat_message(msg["role"], avatar=avatar):
        if msg.get("image_bytes"):
            st.image(
                msg["image_bytes"],
                caption="Uploaded Receipt",
                use_container_width=False,
                width=320,
            )
        st.markdown(msg["content"])


def reset_receipt_session(preserve_onboarding: bool = True):
    """
    Reset receipt analysis and chat memory while preserving onboarding details.
    """
    st.session_state.messages = []
    st.session_state.receipt_uploaded = False
    st.session_state.active_receipt_name = None
    st.session_state.active_receipt_bytes = None
    st.session_state.active_receipt_mime = None
    st.session_state.detected_total = 0.0
    st.session_state.latest_summary = None

    # Re-initialize Gemini chat
    api_key = get_configured_api_key()
    client = get_gemini_client(api_key)
    if client:
        st.session_state.chat = create_gemini_chat(client)
    else:
        st.session_state.chat = None

    # Re-seed welcome message
    welcome_text = WELCOME_MESSAGE_TEMPLATE.format(
        name=st.session_state.user_name or "there"
    )
    add_message("assistant", welcome_text)

# ==============================================================================
# ONBOARDING VIEW
# ==============================================================================
def render_onboarding():
    """Render the clean onboarding view to collect name and email."""
    col_spacer_left, col_card, col_spacer_right = st.columns([1, 2, 1])

    with col_card:
        st.markdown(
            f"""
            <div style="text-align: center; margin-bottom: 2rem; margin-top: 2rem;">
                <span style="font-size: 3.5rem;">🧾</span>
                <h1 style="font-size: 2.2rem; font-weight: 700; margin-bottom: 0.3rem;">{APP_TITLE}</h1>
                <p style="font-size: 1.1rem; color: #64748B; font-weight: 400;">“{APP_TAGLINE}”</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.container(border=True):
            st.subheader("Welcome! Let's get started.")
            st.write(
                "ReceiptSnap AI uses Google Gemini to read your receipts, answer questions, "
                "split expenses fairly, and email you a clean report."
            )
            st.divider()

            with st.form("onboarding_form", clear_on_submit=False):
                name_input = st.text_input(
                    "Your Full Name",
                    placeholder="e.g. Alex Johnson",
                    help="Used to personalize your experience and reports.",
                )
                email_input = st.text_input(
                    "Your Email Address",
                    placeholder="e.g. alex@example.com",
                    help="Receipt summaries will be sent to this email.",
                )

                submit_btn = st.form_submit_button(
                    "Continue to ReceiptSnap 🚀",
                    use_container_width=True,
                    type="primary",
                )

                if submit_btn:
                    clean_name = name_input.strip()
                    clean_email = email_input.strip()

                    errors = []
                    if not validate_name(clean_name):
                        errors.append("Please enter a valid name (at least 2 characters).")
                    if not validate_email(clean_email):
                        errors.append("Please enter a valid email address.")

                    if errors:
                        for err in errors:
                            st.error(err)
                    else:
                        st.session_state.user_name = clean_name
                        st.session_state.user_email = clean_email
                        st.session_state.onboarded = True

                        # Initialize Gemini chat session
                        api_key = get_configured_api_key()
                        client = get_gemini_client(api_key)
                        if client:
                            st.session_state.chat = create_gemini_chat(client)
                        else:
                            st.session_state.chat = None

                        # Add welcome message
                        welcome_text = WELCOME_MESSAGE_TEMPLATE.format(name=clean_name)
                        add_message("assistant", welcome_text)

                        st.rerun()

# ==============================================================================
# MAIN APPLICATION VIEW
# ==============================================================================
def render_main_app():
    """Render the main application after onboarding."""
    api_key = get_configured_api_key()
    client = get_gemini_client(api_key)

    # Re-initialize chat if missing
    if client and st.session_state.chat is None:
        st.session_state.chat = create_gemini_chat(client)

    # --------------------------------------------------------------------------
    # SIDEBAR: User info, Bill Splitter, Receipt Controls
    # --------------------------------------------------------------------------
    with st.sidebar:
        st.markdown(f"### 🧾 {APP_TITLE}")
        st.caption(f"“{APP_TAGLINE}”")

        # Session user card
        with st.container(border=True):
            st.markdown(
                f"""
                **Active Session**  
                👤 **{st.session_state.user_name}**  
                📧 `{st.session_state.user_email}`
                """
            )

        st.divider()

        # Receipt Status
        st.markdown("#### 📄 Active Receipt")
        if st.session_state.receipt_uploaded:
            st.success(f"Loaded: `{st.session_state.active_receipt_name}`")
            if st.session_state.active_receipt_bytes:
                st.image(
                    st.session_state.active_receipt_bytes,
                    caption="Receipt Preview",
                    use_container_width=True,
                )
        else:
            st.info("No receipt uploaded yet. Upload one below or in the main panel.")

        st.divider()

        # Deterministic Bill Splitter
        st.markdown("#### ➗ Deterministic Bill Splitter")
        st.caption("Calculates exact shares in Python without relying on LLM arithmetic.")

        default_total = (
            float(st.session_state.detected_total)
            if st.session_state.detected_total > 0
            else 0.0
        )
        split_total_input = st.number_input(
            "Total Amount (₹ / $)",
            min_value=0.0,
            value=default_total,
            step=1.0,
            format="%.2f",
            help="Total bill amount to split.",
        )
        split_num_people = st.number_input(
            "Number of People",
            min_value=1,
            max_value=100,
            value=2,
            step=1,
            help="Number of people sharing this bill.",
        )

        if st.button("Calculate Equal Split", use_container_width=True, type="secondary"):
            if split_total_input <= 0:
                st.warning("Please enter a total greater than 0.")
            else:
                try:
                    split_res = calculate_equal_split(split_total_input, split_num_people)
                    st.success(f"**₹{split_res['per_person']:.2f}** per person")
                    if split_res["remainder"] != 0:
                        st.caption(
                            f"Rounding remainder: ₹{split_res['remainder']:+.2f} "
                            f"(one person pays the difference for exact total)."
                        )

                    # Update detected total in state
                    st.session_state.detected_total = split_total_input

                    # If chat is active, optionally inform conversation memory
                    if st.session_state.chat:
                        note = (
                            f"[System Note: User calculated an equal split of "
                            f"{split_total_input:.2f} among {split_num_people} people: "
                            f"{split_res['per_person']:.2f} per person.]"
                        )
                        try:
                            st.session_state.chat.send_message(note)
                        except Exception:
                            pass
                except Exception as ex:
                    st.error(f"Error calculating split: {ex}")

        st.divider()

        # Session Reset Button
        if st.button("🔄 Start New Receipt", use_container_width=True):
            reset_receipt_session(preserve_onboarding=True)
            st.rerun()

    # --------------------------------------------------------------------------
    # MAIN AREA: Header & Top Alert Checks
    # --------------------------------------------------------------------------
    col_hdr_left, col_hdr_right = st.columns([3, 1])
    with col_hdr_left:
        st.markdown(
            f"""
            <div class="brand-header">
                <span style="font-size: 2.2rem;">🧾</span>
                <h1 class="brand-title">{APP_TITLE}</h1>
            </div>
            <p class="brand-tagline">“{APP_TAGLINE}”</p>
            """,
            unsafe_allow_html=True,
        )
    with col_hdr_right:
        st.markdown(
            f"""
            <div style="text-align: right; padding-top: 14px;">
                <span class="user-badge">👤 {st.session_state.user_name}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # API Key Configuration Warning (if missing)
    if not api_key:
        st.warning(
            "⚠️ **Gemini API Key Missing**: Please configure `GEMINI_API_KEY` in `.streamlit/secrets.toml` "
            "or set the environment variable. You can still test onboarding and bill splitting."
        )

    # --------------------------------------------------------------------------
    # RECEIPT UPLOAD & QUICK ACTION CARD
    # --------------------------------------------------------------------------
    if not st.session_state.receipt_uploaded:
        with st.container(border=True):
            st.markdown("### 📤 Upload Your Receipt")
            st.write(
                "Upload a clear photo or scan of your receipt (JPG, JPEG, or PNG). "
                "Gemini Vision will extract all visible merchant information, itemized costs, taxes, and totals."
            )

            upload_col1, upload_col2 = st.columns([2, 1])
            with upload_col1:
                uploaded_file = st.file_uploader(
                    "Choose Receipt Image",
                    type=["jpg", "jpeg", "png"],
                    key="initial_receipt_uploader",
                    help="Supported formats: JPG, JPEG, PNG",
                )
            with upload_col2:
                custom_query = st.text_input(
                    "Specific Question (Optional)",
                    placeholder="e.g. How much tax did I pay?",
                    help="Ask a specific question or leave blank for a full breakdown.",
                )

            analyze_clicked = st.button(
                "🔍 Analyze Receipt",
                type="primary",
                use_container_width=True,
                disabled=(uploaded_file is None),
            )

            if analyze_clicked and uploaded_file is not None:
                if not client:
                    st.error(
                        "Gemini API key is not configured. Please add `GEMINI_API_KEY` to `.streamlit/secrets.toml`."
                    )
                else:
                    process_receipt_upload(uploaded_file, custom_query, client)

    # --------------------------------------------------------------------------
    # CONVERSATION HISTORY
    # --------------------------------------------------------------------------
    st.markdown("### 💬 ReceiptSnap Conversation")
    
    # Render all past messages
    for msg in st.session_state.messages:
        render_message(msg)

    # --------------------------------------------------------------------------
    # CHAT INPUT & ACTIVE INTERACTIONS
    # --------------------------------------------------------------------------
    chat_prompt = st.chat_input(
        "Ask a follow-up question (e.g. 'What was the subtotal?', 'Split between 3 people')..."
    )

    if chat_prompt:
        if not client:
            st.error("Cannot process message: Gemini API key is not configured.")
        else:
            handle_chat_followup(chat_prompt, client)

    # --------------------------------------------------------------------------
    # ACTION AREA: EMAIL SUMMARY & REPORT
    # --------------------------------------------------------------------------
    st.write("")
    with st.container(border=True):
        col_act1, col_act2 = st.columns([2, 1])
        with col_act1:
            st.markdown("#### 📧 Email Summary Report")
            st.write(
                f"Generate a clean, structured receipt summary and send it directly to "
                f"**{st.session_state.user_email}** via Gmail SMTP."
            )
        with col_act2:
            st.write("")
            send_email_clicked = st.button(
                "📧 Send Summary to Email",
                type="primary",
                use_container_width=True,
                disabled=not st.session_state.receipt_uploaded,
            )

        if not st.session_state.receipt_uploaded:
            st.caption("ℹ️ Upload and analyze a receipt above to enable email summaries.")

        if send_email_clicked:
            handle_send_email_summary(client)

        if st.session_state.latest_summary:
            with st.expander("📄 View Latest Generated Summary Report", expanded=False):
                st.markdown(st.session_state.latest_summary)

    # --------------------------------------------------------------------------
    # FOOTER
    # --------------------------------------------------------------------------
    st.markdown(
        """
        <div class="footer-note">
            ReceiptSnap AI • AI-generated receipt analysis. Verify important financial details against the original receipt.
        </div>
        """,
        unsafe_allow_html=True,
    )

# ==============================================================================
# WORKFLOW LOGIC: RECEIPT PROCESSING & CHAT
# ==============================================================================
def process_receipt_upload(uploaded_file, custom_query: str, client: genai.Client):
    """
    Handle initial receipt image upload, passing raw bytes to Gemini Vision.
    Supports both IMAGE ONLY and IMAGE + USER TEXT.
    """
    image_bytes = uploaded_file.read()
    image_mime_type = uploaded_file.type or "image/jpeg"

    # Default prompt if user provided none
    prompt_text = (
        custom_query.strip()
        if custom_query and custom_query.strip()
        else "Analyze this receipt and give me a clear breakdown of the visible information."
    )

    # Store receipt in session state
    st.session_state.receipt_uploaded = True
    st.session_state.active_receipt_name = uploaded_file.name
    st.session_state.active_receipt_bytes = image_bytes
    st.session_state.active_receipt_mime = image_mime_type

    # Append user upload message to UI history
    add_message(
        role="user",
        content=prompt_text,
        image_bytes=image_bytes,
        mime_type=image_mime_type,
    )

    # Send to Gemini
    with st.spinner("Analyzing receipt with Gemini Vision..."):
        try:
            image_part = types.Part.from_bytes(
                data=image_bytes,
                mime_type=image_mime_type,
            )

            if st.session_state.chat is None:
                st.session_state.chat = create_gemini_chat(client)

            response = st.session_state.chat.send_message([image_part, prompt_text])
            response_text = get_safe_response_text(response)

            add_message(role="assistant", content=response_text)

            # Auto-extract total for convenience in Bill Splitter
            detected = extract_estimated_total(response_text)
            if detected:
                st.session_state.detected_total = detected

        except Exception as e:
            err_msg = str(e)
            if "not found" in err_msg.lower() or "unsupported" in err_msg.lower():
                user_msg = (
                    f"The configured model `{MODEL_NAME}` is currently unavailable. "
                    f"Please update `MODEL_NAME` in `app.py`."
                )
            else:
                user_msg = "Gemini is temporarily unavailable. Please verify your connection or try again in a moment."
            add_message(role="assistant", content=f"⚠️ {user_msg}")

    st.rerun()


def handle_chat_followup(chat_prompt: str, client: genai.Client):
    """Handle follow-up questions using Gemini conversation memory."""
    # Append user question to history
    add_message(role="user", content=chat_prompt)

    with st.spinner("Thinking..."):
        try:
            if st.session_state.chat is None:
                st.session_state.chat = create_gemini_chat(client)

            response = st.session_state.chat.send_message(chat_prompt)
            response_text = get_safe_response_text(response)
            add_message(role="assistant", content=response_text)

            # Check if total mentioned in answer
            detected = extract_estimated_total(response_text)
            if detected:
                st.session_state.detected_total = detected

        except Exception as e:
            err_msg = str(e)
            if "not found" in err_msg.lower() or "unsupported" in err_msg.lower():
                user_msg = f"The model `{MODEL_NAME}` is currently unavailable. Please verify configuration."
            else:
                user_msg = "Gemini is temporarily unavailable. Please try again in a moment."
            add_message(role="assistant", content=f"⚠️ {user_msg}")

    st.rerun()


def handle_send_email_summary(client: genai.Client):
    """
    Generate email summary via hidden prompt and deliver using Gmail SMTP.
    """
    if not st.session_state.receipt_uploaded:
        st.warning("Please upload and analyze a receipt first.")
        return

    # Check Gmail credentials from secrets
    gmail_user = st.secrets.get("GMAIL_ADDRESS", "")
    gmail_pwd = st.secrets.get("GMAIL_APP_PASSWORD", "")

    with st.spinner("Generating email summary report..."):
        try:
            if st.session_state.chat is None and client:
                st.session_state.chat = create_gemini_chat(client)

            if not st.session_state.chat:
                st.error("Gemini chat session is not available. Please verify your Gemini API key.")
                return

            # Request summary from Gemini using the hidden prompt
            summary_response = st.session_state.chat.send_message(SUMMARY_REQUEST_PROMPT)
            summary_text = get_safe_response_text(summary_response)
            st.session_state.latest_summary = summary_text

        except Exception as e:
            st.error("Failed to generate summary with Gemini. Please try again.")
            return

    # Deliver via SMTP
    with st.spinner(f"Sending email to {st.session_state.user_email}..."):
        subject = f"ReceiptSnap AI — Receipt Summary ({st.session_state.active_receipt_name or 'Receipt'})"
        success, message = send_email_smtp(
            recipient=st.session_state.user_email,
            subject=subject,
            body=summary_text,
            gmail_user=gmail_user,
            gmail_password=gmail_pwd,
        )

        if success:
            st.success(f"✅ Receipt summary sent successfully to {st.session_state.user_email}!")
        else:
            st.warning(f"⚠️ {message}")
            st.info(
                "You can still view or copy your generated summary below. "
                "To enable email delivery, add `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` to `.streamlit/secrets.toml`."
            )

# ==============================================================================
# APPLICATION ENTRYPOINT
# ==============================================================================
def main():
    initialize_session_state()

    if not st.session_state.onboarded:
        render_onboarding()
    else:
        render_main_app()


if __name__ == "__main__":
    main()
