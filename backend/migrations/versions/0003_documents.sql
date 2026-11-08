
CREATE TABLE document_text_blocks (
	version_id VARCHAR(36) NOT NULL, 
	ordinal INTEGER NOT NULL, 
	kind VARCHAR(24) NOT NULL, 
	locator VARCHAR(120) NOT NULL, 
	page INTEGER, 
	text TEXT NOT NULL, 
	structure JSONB NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT document_block_once UNIQUE (version_id, ordinal), 
	FOREIGN KEY(version_id) REFERENCES document_versions (id)
)


-- statement-break
CREATE INDEX ix_document_text_blocks_version_id ON document_text_blocks (version_id)
-- statement-break

CREATE TABLE documents (
	department_id VARCHAR(36) NOT NULL, 
	created_by VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id), 
	FOREIGN KEY(created_by) REFERENCES user_accounts (id)
)


-- statement-break
CREATE INDEX ix_documents_department_id ON documents (department_id)
-- statement-break

CREATE TABLE extracted_fragments (
	anchor_id VARCHAR(36) NOT NULL, 
	block_id VARCHAR(36) NOT NULL, 
	version_id VARCHAR(36) NOT NULL, 
	ordinal INTEGER NOT NULL, 
	start INTEGER NOT NULL, 
	"end" INTEGER NOT NULL, 
	normalized_text TEXT NOT NULL, 
	PRIMARY KEY (anchor_id), 
	CONSTRAINT document_fragment_once UNIQUE (version_id, ordinal), 
	CHECK ("start" >= 0 AND "end" > "start"), 
	FOREIGN KEY(anchor_id) REFERENCES source_anchors (id), 
	FOREIGN KEY(block_id) REFERENCES document_text_blocks (id), 
	FOREIGN KEY(version_id) REFERENCES document_versions (id)
)


-- statement-break
CREATE INDEX ix_extracted_fragments_block_id ON extracted_fragments (block_id)
-- statement-break
CREATE INDEX ix_extracted_fragments_version_id ON extracted_fragments (version_id)
-- statement-break

CREATE TABLE document_jobs (
	version_id VARCHAR(36) NOT NULL, 
	actor_id VARCHAR(36) NOT NULL, 
	session_id VARCHAR(36) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	version INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	published_at TIMESTAMP WITH TIME ZONE, 
	due_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	lease_token VARCHAR(36), 
	attempts INTEGER NOT NULL, 
	dispatch_attempts INTEGER NOT NULL, 
	error_code VARCHAR(64), 
	warnings JSONB NOT NULL, 
	block_count INTEGER NOT NULL, 
	fragment_count INTEGER NOT NULL, 
	page_count INTEGER, 
	parser_version VARCHAR(80) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (status IN ('queued','processing','ready','partial','needs_ocr','failed','cancelled','access_revoked')), 
	UNIQUE (version_id), 
	FOREIGN KEY(version_id) REFERENCES document_versions (id), 
	FOREIGN KEY(actor_id) REFERENCES user_accounts (id), 
	FOREIGN KEY(session_id) REFERENCES browser_sessions (id)
)


-- statement-break
CREATE INDEX ix_document_jobs_status ON document_jobs (status)
-- statement-break

CREATE TABLE document_originals (
	version_id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36) NOT NULL, 
	sequence INTEGER NOT NULL, 
	uploaded_by VARCHAR(36) NOT NULL, 
	filename VARCHAR(160) NOT NULL, 
	media_type VARCHAR(100) NOT NULL, 
	format VARCHAR(8) NOT NULL, 
	size INTEGER NOT NULL, 
	content BYTEA NOT NULL, 
	document_kind VARCHAR(32) NOT NULL, 
	document_date DATE, 
	external_policy VARCHAR(32) NOT NULL, 
	idempotency_key VARCHAR(80) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	PRIMARY KEY (version_id), 
	CONSTRAINT document_revision_once UNIQUE (document_id, sequence), 
	CONSTRAINT document_upload_size CHECK (size > 0 AND size <= 10485760), 
	CHECK (format IN ('docx','pdf')), 
	CHECK (external_policy IN ('prohibited','synthetic_allowed')), 
	FOREIGN KEY(version_id) REFERENCES document_versions (id), 
	FOREIGN KEY(document_id) REFERENCES documents (id), 
	FOREIGN KEY(uploaded_by) REFERENCES user_accounts (id), 
	UNIQUE (idempotency_key)
)


-- statement-break
CREATE INDEX ix_document_originals_document_id ON document_originals (document_id)