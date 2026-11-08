
CREATE TABLE index_receipts (
	version_id VARCHAR(36) NOT NULL, 
	index_key VARCHAR(100) NOT NULL, 
	source_digest VARCHAR(64) NOT NULL, 
	chunk_count INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (version_id, index_key), 
	FOREIGN KEY(version_id) REFERENCES document_versions (id)
)


-- statement-break

CREATE TABLE document_corpora (
	title VARCHAR(180) NOT NULL, 
	as_of DATE NOT NULL, 
	declared_complete BOOLEAN NOT NULL, 
	created_by VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	limitations JSONB NOT NULL, 
	fragment_count INTEGER NOT NULL, 
	digest VARCHAR(64) NOT NULL, 
	idempotency_key VARCHAR(80) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by) REFERENCES user_accounts (id), 
	UNIQUE (idempotency_key)
)


-- statement-break

CREATE TABLE search_chunks (
	anchor_id VARCHAR(36) NOT NULL, 
	index_key VARCHAR(100) NOT NULL, 
	ordinal INTEGER NOT NULL, 
	start INTEGER NOT NULL, 
	"end" INTEGER NOT NULL, 
	text_hash VARCHAR(64) NOT NULL, 
	embedding VECTOR(384) NOT NULL, 
	PRIMARY KEY (anchor_id, index_key, ordinal), 
	CHECK ("start" >= 0 AND "end" > "start"), 
	FOREIGN KEY(anchor_id) REFERENCES source_anchors (id)
)


-- statement-break

CREATE TABLE comparison_runs (
	title VARCHAR(180) NOT NULL, 
	before_id VARCHAR(36) NOT NULL, 
	after_id VARCHAR(36) NOT NULL, 
	criteria TEXT NOT NULL, 
	created_by VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	algorithm_version VARCHAR(100) NOT NULL, 
	index_key VARCHAR(100) NOT NULL, 
	model_manifest JSONB NOT NULL, 
	access_snapshot JSONB NOT NULL, 
	registry_snapshot JSONB NOT NULL, 
	prompt_version VARCHAR(40) NOT NULL, 
	limitations JSONB NOT NULL, 
	idempotency_key VARCHAR(80) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (before_id <> after_id), 
	FOREIGN KEY(before_id) REFERENCES document_corpora (id), 
	FOREIGN KEY(after_id) REFERENCES document_corpora (id), 
	FOREIGN KEY(created_by) REFERENCES user_accounts (id), 
	UNIQUE (idempotency_key)
)


-- statement-break

CREATE TABLE corpus_members (
	corpus_id VARCHAR(36) NOT NULL, 
	version_id VARCHAR(36) NOT NULL, 
	snapshot JSONB NOT NULL, 
	PRIMARY KEY (corpus_id, version_id), 
	FOREIGN KEY(corpus_id) REFERENCES document_corpora (id), 
	FOREIGN KEY(version_id) REFERENCES document_versions (id)
)


-- statement-break

CREATE TABLE corpus_sources (
	corpus_id VARCHAR(36) NOT NULL, 
	anchor_id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (corpus_id, anchor_id), 
	FOREIGN KEY(corpus_id) REFERENCES document_corpora (id), 
	FOREIGN KEY(anchor_id) REFERENCES source_anchors (id)
)


-- statement-break

CREATE TABLE analysis_jobs (
	kind VARCHAR(16) NOT NULL, 
	index_key VARCHAR(100) NOT NULL, 
	run_id VARCHAR(36), 
	version_ids JSONB NOT NULL, 
	actor_id VARCHAR(36) NOT NULL, 
	session_id VARCHAR(36) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	version INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	published_at TIMESTAMP WITH TIME ZONE, 
	due_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	lease_token VARCHAR(36), 
	attempts INTEGER NOT NULL, 
	dispatch_attempts INTEGER NOT NULL, 
	error_code VARCHAR(48), 
	result_summary JSONB NOT NULL, 
	idempotency_key VARCHAR(80) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (kind IN ('index','compare')), 
	CHECK (status IN ('queued','processing','ready','failed','cancelled','access_revoked')), 
	UNIQUE (run_id), 
	FOREIGN KEY(run_id) REFERENCES comparison_runs (id), 
	FOREIGN KEY(actor_id) REFERENCES user_accounts (id), 
	FOREIGN KEY(session_id) REFERENCES browser_sessions (id), 
	UNIQUE (idempotency_key)
)


-- statement-break
CREATE INDEX ix_analysis_jobs_status ON analysis_jobs (status)
-- statement-break

CREATE TABLE comparison_findings (
	run_id VARCHAR(36) NOT NULL, 
	ordinal INTEGER NOT NULL, 
	category VARCHAR(40) NOT NULL, 
	title VARCHAR(240) NOT NULL, 
	explanation TEXT NOT NULL, 
	before_ids JSONB NOT NULL, 
	after_ids JSONB NOT NULL, 
	provenance VARCHAR(40) NOT NULL, 
	method VARCHAR(160) NOT NULL, 
	limitations JSONB NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (run_id, ordinal), 
	FOREIGN KEY(run_id) REFERENCES comparison_runs (id)
)


-- statement-break
CREATE INDEX ix_comparison_findings_run_id ON comparison_findings (run_id)
-- statement-break

CREATE TABLE finding_reviews (
	finding_id VARCHAR(36) NOT NULL, 
	version INTEGER NOT NULL, 
	decision VARCHAR(32) NOT NULL, 
	category VARCHAR(40) NOT NULL, 
	note TEXT NOT NULL, 
	author_id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	idempotency_key VARCHAR(80) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (finding_id, version), 
	FOREIGN KEY(finding_id) REFERENCES comparison_findings (id), 
	FOREIGN KEY(author_id) REFERENCES user_accounts (id), 
	UNIQUE (idempotency_key)
)


-- statement-break
CREATE INDEX ix_finding_reviews_finding_id ON finding_reviews (finding_id)
