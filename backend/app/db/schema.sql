CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    analysis_mode TEXT NOT NULL,
    selected_experts_json TEXT NOT NULL,
    subject_json TEXT NOT NULL,
    human_review_status TEXT NOT NULL DEFAULT 'not_required',
    pending_human_issue_ids_json TEXT NOT NULL DEFAULT '[]',
    report_summary TEXT NOT NULL DEFAULT '',
    failure_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    duration_seconds REAL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_events (
    event_id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    phase TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL,
    issue_id TEXT NOT NULL DEFAULT '',
    expert_id TEXT NOT NULL DEFAULT '',
    message_type TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL,
    expert_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    finding_type TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issues (
    issue_id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    label_id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL,
    issue_id TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'human',
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    doc_id TEXT PRIMARY KEY,
    expert_id TEXT NOT NULL,
    title TEXT NOT NULL,
    doc_type TEXT NOT NULL DEFAULT 'reference',
    content TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    source_filename TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_document_nodes (
    node_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    expert_id TEXT NOT NULL,
    parent_node_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    level INTEGER NOT NULL DEFAULT 1,
    line_start INTEGER NOT NULL DEFAULT 1,
    line_end INTEGER NOT NULL DEFAULT 1,
    summary TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    keywords_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS experts (
    expert_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_settings (
    settings_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
