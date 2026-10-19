-- =====================================================================================
-- MediShield Intake: MySQL schema (structure only, no data)
--
-- Source of truth: backend/app/db.py (SCHEMA + MIGRATIONS + POST_MIGRATIONS), with the
-- migration columns folded into each CREATE TABLE. Safe to run more than once: every
-- statement uses IF NOT EXISTS, and existing tables and rows are never changed.
--
-- Run (MySQL 8.0+):
--     mysql -u root -p < database/schema.sql
--
-- The database name must match MYSQL_DB in .env (default: medishield). If you change it,
-- edit the two lines below.
-- =====================================================================================

CREATE DATABASE IF NOT EXISTS `medishield` CHARACTER SET utf8mb4;
USE `medishield`;


-- -------------------------------------------------------------------------------------
-- Reference data: insured members, their ID documents, registered patients
-- -------------------------------------------------------------------------------------

-- Insured members (seeded by backend/scripts/seed_members.py). KYC matches claimants here.
CREATE TABLE IF NOT EXISTS member (
    member_id     VARCHAR(32)  PRIMARY KEY,
    full_name     VARCHAR(120) NOT NULL,
    date_of_birth DATE         NULL,
    policy_number VARCHAR(32)  NOT NULL,
    plan          VARCHAR(16)  NULL,                 -- GOLD / SILVER (from the policy prefix)
    INDEX ix_member_policy (policy_number)
);

-- The member's identity document: type, expiry and status.
CREATE TABLE IF NOT EXISTS kyc (
    member_id      VARCHAR(32) PRIMARY KEY,
    id_type        VARCHAR(32) NULL,
    id_expiry_date DATE        NULL,
    id_status      VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'
);

-- Patients registered in the UI (or by seed_patients.py), linked to a member.
CREATE TABLE IF NOT EXISTS patient (
    patient_id    VARCHAR(36)  PRIMARY KEY,
    member_id     VARCHAR(32)  NULL,
    full_name     VARCHAR(120) NOT NULL,
    date_of_birth DATE         NULL,
    gender        VARCHAR(16)  NULL,
    phone         VARCHAR(32)  NULL,
    email         VARCHAR(255) NULL,
    address       VARCHAR(255) NULL,
    policy_number VARCHAR(32)  NULL,
    created_by    VARCHAR(64)  NOT NULL,
    created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_by    VARCHAR(64)  NULL,
    updated_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX ix_patient_name (full_name),
    INDEX ix_patient_policy (policy_number)
);


-- -------------------------------------------------------------------------------------
-- Claims: one claim_bundle per claim, one case_record per page
-- -------------------------------------------------------------------------------------

-- A claim (all documents uploaded together) and its single decision.
CREATE TABLE IF NOT EXISTS claim_bundle (
    bundle_id    VARCHAR(36)  PRIMARY KEY,
    title        VARCHAR(160) NOT NULL,
    patient_id   VARCHAR(36)  NULL,
    created_by   VARCHAR(64)  NOT NULL,
    created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_by   VARCHAR(64)  NULL,
    updated_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    status       VARCHAR(24)  NULL,                  -- QUEUED, PROCESSING, NEEDS_REVIEW, ON_HOLD, ...
    decision     VARCHAR(12)  NULL,                  -- APPROVE / REJECT / ESCALATE
    confidence   DECIMAL(4,3) NULL,
    needs_review BOOLEAN      NOT NULL DEFAULT FALSE,
    result       JSON         NULL,                  -- every agent's output
    override     JSON         NULL,                  -- the reviewer's decision and note
    INDEX ix_bundle_patient (patient_id),
    INDEX ix_bundle_status (status)
);

-- One uploaded page (or a single-document case of the older /api/cases pipeline).
CREATE TABLE IF NOT EXISTS case_record (
    case_id      VARCHAR(36)  PRIMARY KEY,
    file_name    VARCHAR(255) NOT NULL,
    doc_type     VARCHAR(32)  NULL,
    status       VARCHAR(24)  NOT NULL,
    decision     VARCHAR(12)  NULL,
    confidence   DECIMAL(4,3) NULL,
    needs_review BOOLEAN      NOT NULL DEFAULT FALSE,
    result       JSON         NULL,
    override     JSON         NULL,
    created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    patient_id   VARCHAR(36)  NULL,
    created_by   VARCHAR(64)  NULL,
    updated_by   VARCHAR(64)  NULL,
    bundle_id    VARCHAR(36)  NULL,                  -- the claim_bundle this page belongs to
    INDEX ix_case_patient (patient_id),
    INDEX ix_case_bundle (bundle_id)
);

-- Billing facts of a decided claim; used for the duplicate-claim check.
CREATE TABLE IF NOT EXISTS claim (
    case_id      VARCHAR(36)   PRIMARY KEY,
    member_id    VARCHAR(32)   NULL,
    patient_name VARCHAR(120)  NULL,
    service_date DATE          NULL,
    claim_amount DECIMAL(12,2) NULL,
    icd10_codes  JSON          NULL,
    cpt_codes    JSON          NULL,
    provider_npi VARCHAR(10)   NULL,
    created_at   TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    INDEX ix_claim_member (member_id)
);

-- Stays and prescriptions of a member; used for the 30-day readmission check.
CREATE TABLE IF NOT EXISTS clinical_event (
    id                INT          AUTO_INCREMENT PRIMARY KEY,
    case_id           VARCHAR(36)  NOT NULL,
    member_id         VARCHAR(32)  NULL,
    admission_date    DATE         NULL,
    discharge_date    DATE         NULL,
    prescription_date DATE         NULL,
    diagnosis         VARCHAR(255) NULL,
    INDEX ix_clinical_member (member_id)
);


-- -------------------------------------------------------------------------------------
-- Workflow state: LangGraph checkpoints (backend/app/checkpoint_mysql.py)
-- A claim on hold or waiting for human review is paused here and survives restarts.
-- -------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS lg_checkpoint (
    thread_id     VARCHAR(80)  NOT NULL,
    checkpoint_ns VARCHAR(255) NOT NULL DEFAULT '',
    checkpoint_id VARCHAR(64)  NOT NULL,
    parent_id     VARCHAR(64)  NULL,
    type          VARCHAR(32)  NOT NULL,
    checkpoint    LONGBLOB     NOT NULL,
    meta_type     VARCHAR(32)  NOT NULL,
    metadata      LONGBLOB     NOT NULL,
    created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS lg_checkpoint_blob (
    thread_id     VARCHAR(80)  NOT NULL,
    checkpoint_ns VARCHAR(255) NOT NULL DEFAULT '',
    channel       VARCHAR(128) NOT NULL,
    version       VARCHAR(64)  NOT NULL,
    type          VARCHAR(32)  NOT NULL,
    value         LONGBLOB     NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

CREATE TABLE IF NOT EXISTS lg_checkpoint_write (
    thread_id     VARCHAR(80)  NOT NULL,
    checkpoint_ns VARCHAR(255) NOT NULL DEFAULT '',
    checkpoint_id VARCHAR(64)  NOT NULL,
    task_id       VARCHAR(64)  NOT NULL,
    idx           INT          NOT NULL,
    channel       VARCHAR(128) NOT NULL,
    type          VARCHAR(32)  NOT NULL,
    value         LONGBLOB     NOT NULL,
    task_path     VARCHAR(255) NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);


-- -------------------------------------------------------------------------------------
-- Audit and operations
-- -------------------------------------------------------------------------------------

-- Every agent step and human action, timestamped to the millisecond.
CREATE TABLE IF NOT EXISTS audit_event (
    id          BIGINT      AUTO_INCREMENT PRIMARY KEY,
    entity_type VARCHAR(16) NOT NULL DEFAULT 'claim',
    entity_id   VARCHAR(64) NOT NULL DEFAULT '',
    claim_id    VARCHAR(36) NULL,
    doc_id      VARCHAR(36) NULL,
    actor       VARCHAR(64) NOT NULL,
    event       VARCHAR(48) NOT NULL,
    status      VARCHAR(16) NOT NULL,
    detail      JSON        NULL,
    duration_ms INT         NULL,
    created_at  DATETIME(3) NOT NULL,
    INDEX ix_audit_claim (claim_id, id),
    INDEX ix_audit_entity (entity_type, entity_id, id)
);

-- Application logs (backend/app/logdb.py): API, uvicorn and scripts write here, not to files.
CREATE TABLE IF NOT EXISTS app_log (
    id         BIGINT       AUTO_INCREMENT PRIMARY KEY,
    created_at DATETIME(3)  NOT NULL,
    level      VARCHAR(10)  NOT NULL,
    logger     VARCHAR(128) NOT NULL,
    source     VARCHAR(48)  NOT NULL,
    message    MEDIUMTEXT   NOT NULL,
    exc_text   MEDIUMTEXT   NULL,
    INDEX ix_log_time (created_at),
    INDEX ix_log_level (level, id),
    INDEX ix_log_source (source, id)
);

-- Model outputs per page (transcription, document type, fields), keyed by input + model + prompt.
CREATE TABLE IF NOT EXISTS model_cache (
    cache_key  CHAR(80)     PRIMARY KEY,
    kind       VARCHAR(16)  NOT NULL,
    model      VARCHAR(128) NOT NULL,
    payload    JSON         NOT NULL,
    created_at TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

-- Sign-in accounts. Passwords are PBKDF2 hashes: create them with backend/scripts/create_users.py.
CREATE TABLE IF NOT EXISTS app_user (
    username      VARCHAR(64)  PRIMARY KEY,
    password_hash VARCHAR(255) NOT NULL,
    role          ENUM('admin','agent') NOT NULL,
    full_name     VARCHAR(120) NOT NULL,
    created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

-- Policy PDFs ingested into the Qdrant vector store.
CREATE TABLE IF NOT EXISTS policy_document (
    id          INT          AUTO_INCREMENT PRIMARY KEY,
    filename    VARCHAR(255) NOT NULL,
    chunks      INT          NOT NULL,
    ingested_at TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);
