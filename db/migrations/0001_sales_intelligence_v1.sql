-- =====================================================================================
-- Transformativa Revenue Engine — Data Contract V1.0
-- Migration 0001 — schema `sales_intelligence`
--
-- Fonte (autoridade, na ordem): BASELINE V1.1.0 docs 12 > 04 > 06 > 03 > 05.
--   doc 04 — Projeto Físico de Dados (DDL, índices, deduplicação)
--   doc 05 — Modelo Entidade-Relacionamento (IDs canônicos, mapa Odoo)
--   doc 12 — Contratos e Governança (source of truth, eventos, estados, compliance)
--   doc 06 — Integrações e Fluxos Sistêmicos (eventos, idempotência, reconciliação)
--   doc 03 — Arquitetura de Negócio (scores, tiering, faixas, vocabulários)
--
-- Banco: odoo (operação) | base transformativa_ai, schema sales_intelligence (inteligência)
--
-- STATUS: especificação congelada (Data Contract V1.0). Esta DDL NÃO foi aplicada em
-- nenhum ambiente. Pela ADR-005 (nenhuma DDL nasce em produção), a aplicação segue
-- dev -> homologação -> produção, com o runner de migração versionado do W1.
-- =====================================================================================

CREATE SCHEMA IF NOT EXISTS sales_intelligence;

-- -------------------------------------------------------------------------------------
-- 1. organizations — empresa pesquisada (dono: PostgreSQL)
-- -------------------------------------------------------------------------------------
CREATE TABLE sales_intelligence.organizations (
    id UUID PRIMARY KEY,
    odoo_partner_id BIGINT UNIQUE,
    legal_name VARCHAR(255),
    trade_name VARCHAR(255),
    domain VARCHAR(255),
    website_url TEXT,
    linkedin_url TEXT,
    cnpj VARCHAR(20),
    industry_code VARCHAR(100),
    industry_name VARCHAR(255),
    employee_count INTEGER,
    employee_band VARCHAR(30),
    revenue_estimate NUMERIC(18,2),
    unit_count INTEGER,
    city VARCHAR(120),
    state VARCHAR(80),
    country_code CHAR(2) DEFAULT 'BR',
    business_model VARCHAR(50),
    status VARCHAR(30) NOT NULL DEFAULT 'DISCOVERED',
    source VARCHAR(100),
    data_quality_score NUMERIC(5,2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

-- -------------------------------------------------------------------------------------
-- 2. contacts — contato comercial (dono operacional: Odoo; registro de inteligência: PG)
-- -------------------------------------------------------------------------------------
CREATE TABLE sales_intelligence.contacts (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES sales_intelligence.organizations(id),
    odoo_partner_id BIGINT UNIQUE,
    first_name VARCHAR(120),
    last_name VARCHAR(120),
    full_name VARCHAR(255),
    job_title VARCHAR(255),
    department VARCHAR(100),
    seniority VARCHAR(50),
    decision_role VARCHAR(50),
    linkedin_url TEXT,
    email VARCHAR(320),
    email_status VARCHAR(30),
    phone VARCHAR(50),
    whatsapp VARCHAR(50),
    preferred_channel VARCHAR(30),
    influence_score NUMERIC(5,2),
    contactability_score NUMERIC(5,2),
    relationship_score NUMERIC(5,2),
    legal_basis VARCHAR(50),
    do_not_contact BOOLEAN DEFAULT FALSE,
    opt_out_email BOOLEAN DEFAULT FALSE,
    opt_out_whatsapp BOOLEAN DEFAULT FALSE,
    source VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------------------------------------
-- 3. signals — sinais de mudança/demanda (dono: PostgreSQL)
-- -------------------------------------------------------------------------------------
CREATE TABLE sales_intelligence.signals (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES sales_intelligence.organizations(id),
    signal_type VARCHAR(80) NOT NULL,
    signal_category VARCHAR(80),
    title VARCHAR(500),
    description TEXT,
    source_type VARCHAR(80),
    source_url TEXT,
    event_date TIMESTAMPTZ,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confidence NUMERIC(5,4),
    relevance_score NUMERIC(5,2),
    buying_signal_points NUMERIC(6,2),
    decay_factor NUMERIC(6,4) DEFAULT 1,
    expires_at TIMESTAMPTZ,
    evidence JSONB,
    research_run_id UUID,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------------------------------------
-- 4. research_runs — execução de pesquisa por agente (dono: PostgreSQL)
-- -------------------------------------------------------------------------------------
CREATE TABLE sales_intelligence.research_runs (
    id UUID PRIMARY KEY,
    organization_id UUID REFERENCES sales_intelligence.organizations(id),
    agent_name VARCHAR(100) NOT NULL,
    agent_version VARCHAR(50),
    workflow_name VARCHAR(150),
    workflow_version VARCHAR(50),
    model_provider VARCHAR(80),
    model_name VARCHAR(120),
    prompt_version VARCHAR(80),
    research_type VARCHAR(80),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    status VARCHAR(30),
    source_count INTEGER DEFAULT 0,
    confidence NUMERIC(5,4),
    summary TEXT,
    structured_output JSONB,
    input_hash VARCHAR(128),
    tokens_input INTEGER,
    tokens_output INTEGER,
    estimated_cost NUMERIC(12,6),
    error_code VARCHAR(100),
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------------------------------------
-- 5. pain_hypotheses — hipótese de dor (dono: PostgreSQL; inferência marcada como inferência)
-- -------------------------------------------------------------------------------------
CREATE TABLE sales_intelligence.pain_hypotheses (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES sales_intelligence.organizations(id),
    research_run_id UUID REFERENCES sales_intelligence.research_runs(id),
    pain_category VARCHAR(100),
    pain_statement TEXT NOT NULL,
    evidence_summary TEXT,
    evidence JSONB,
    confidence NUMERIC(5,4),
    business_impact_score NUMERIC(5,2),
    estimated_impact_description TEXT,
    status VARCHAR(30) DEFAULT 'HYPOTHESIS',
    validated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------------------------------------
-- 6. scores — score calculado, versionado (dono: PostgreSQL)
-- -------------------------------------------------------------------------------------
CREATE TABLE sales_intelligence.scores (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES sales_intelligence.organizations(id),
    score_type VARCHAR(50) NOT NULL,
    score_value NUMERIC(5,2) NOT NULL,
    score_version VARCHAR(30) NOT NULL,
    inputs JSONB,
    explanation JSONB,
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until TIMESTAMPTZ
);

-- -------------------------------------------------------------------------------------
-- 7. interactions — interação em qualquer canal (dono: PostgreSQL para histórico)
-- -------------------------------------------------------------------------------------
CREATE TABLE sales_intelligence.interactions (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES sales_intelligence.organizations(id),
    contact_id UUID REFERENCES sales_intelligence.contacts(id),
    odoo_lead_id BIGINT,
    channel VARCHAR(30) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    interaction_type VARCHAR(50),
    occurred_at TIMESTAMPTZ NOT NULL,
    subject TEXT,
    content_summary TEXT,
    content_reference TEXT,
    sentiment VARCHAR(30),
    intent VARCHAR(50),
    response_category VARCHAR(50),
    ai_confidence NUMERIC(5,4),
    campaign_id UUID,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------------------------------------
-- 8. recommendations — próximo passo recomendado (dono: PostgreSQL; estado em doc 12 §5)
-- -------------------------------------------------------------------------------------
CREATE TABLE sales_intelligence.recommendations (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES sales_intelligence.organizations(id),
    contact_id UUID REFERENCES sales_intelligence.contacts(id),
    opportunity_id UUID,
    recommendation_type VARCHAR(80),
    action VARCHAR(120),
    description TEXT,
    rationale TEXT,
    confidence NUMERIC(5,4),
    priority INTEGER,
    status VARCHAR(30) DEFAULT 'OPEN',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    due_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    executed_at TIMESTAMPTZ,
    execution_result JSONB
);

-- -------------------------------------------------------------------------------------
-- 9. agent_runs — auditoria de execução de agente (dono: PostgreSQL)
-- -------------------------------------------------------------------------------------
CREATE TABLE sales_intelligence.agent_runs (
    id UUID PRIMARY KEY,
    agent_name VARCHAR(100) NOT NULL,
    agent_role VARCHAR(80),
    agent_version VARCHAR(50),
    workflow VARCHAR(150),
    workflow_version VARCHAR(50),
    organization_id UUID,
    triggered_by VARCHAR(80),
    correlation_id UUID,
    input JSONB,
    output JSONB,
    model VARCHAR(120),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    status VARCHAR(30),
    tokens_input INTEGER,
    tokens_output INTEGER,
    estimated_cost NUMERIC(12,6),
    error JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------------------------------------
-- 10. outbox_events — Outbox Pattern: PG -> n8n -> Odoo (dono: PostgreSQL; ADR-0003)
-- -------------------------------------------------------------------------------------
CREATE TABLE sales_intelligence.outbox_events (
    id UUID PRIMARY KEY,
    aggregate_type VARCHAR(80),
    aggregate_id UUID,
    event_type VARCHAR(100),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    attempts INTEGER DEFAULT 0,
    status VARCHAR(30) DEFAULT 'PENDING',
    last_error TEXT
);

-- -------------------------------------------------------------------------------------
-- 11. sync_events — trilha de sincronização e idempotência (dono: PostgreSQL)
-- -------------------------------------------------------------------------------------
CREATE TABLE sales_intelligence.sync_events (
    id UUID PRIMARY KEY,
    entity_type VARCHAR(80),
    entity_id UUID,
    source_system VARCHAR(50),
    target_system VARCHAR(50),
    operation VARCHAR(30),
    source_version VARCHAR(100),
    idempotency_key VARCHAR(255) UNIQUE,
    status VARCHAR(30),
    request_payload JSONB,
    response_payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    error_message TEXT
);

-- -------------------------------------------------------------------------------------
-- 12. human_approvals — aprovação humana registrada (dono: PostgreSQL; ADR-0004)
-- -------------------------------------------------------------------------------------
CREATE TABLE sales_intelligence.human_approvals (
    id UUID PRIMARY KEY,
    action_type VARCHAR(80),
    entity_type VARCHAR(80),
    entity_id UUID,
    requested_by VARCHAR(100),
    proposed_action JSONB NOT NULL,
    status VARCHAR(30) DEFAULT 'PENDING',
    requested_at TIMESTAMPTZ DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    decided_by VARCHAR(120),
    decision_notes TEXT
);

-- =====================================================================================
-- Índices mínimos (doc 04 §15)
-- =====================================================================================
CREATE INDEX idx_organizations_domain ON sales_intelligence.organizations (domain);
CREATE INDEX idx_organizations_cnpj ON sales_intelligence.organizations (cnpj);
CREATE INDEX idx_organizations_employee_band ON sales_intelligence.organizations (employee_band);
CREATE INDEX idx_organizations_industry_code ON sales_intelligence.organizations (industry_code);
CREATE INDEX idx_organizations_state_city ON sales_intelligence.organizations (state, city);
CREATE INDEX idx_contacts_organization_id ON sales_intelligence.contacts (organization_id);
CREATE INDEX idx_contacts_email ON sales_intelligence.contacts (email);
CREATE INDEX idx_contacts_decision_role ON sales_intelligence.contacts (decision_role);
CREATE INDEX idx_signals_organization_id ON sales_intelligence.signals (organization_id);
CREATE INDEX idx_signals_signal_type ON sales_intelligence.signals (signal_type);
CREATE INDEX idx_signals_detected_at ON sales_intelligence.signals (detected_at DESC);
CREATE INDEX idx_scores_org_type_calc ON sales_intelligence.scores (organization_id, score_type, calculated_at DESC);
CREATE INDEX idx_interactions_org_occurred ON sales_intelligence.interactions (organization_id, occurred_at DESC);
CREATE INDEX idx_recommendations_org_status ON sales_intelligence.recommendations (organization_id, status);
CREATE INDEX idx_outbox_events_status_created ON sales_intelligence.outbox_events (status, created_at);
-- sync_events(idempotency_key) já é índice implícito pela restrição UNIQUE.
-- contacts.id é o UUID canônico: o vínculo com Odoo é o odoo_partner_id, nunca o inverso (doc 05 §4).

-- =====================================================================================
-- Vínculos lógicos declarados no baseline SEM chave estrangeira na V1.
-- Não inventamos FK onde o contrato não a define; ficam registrados para decisão do W1:
--   signals.research_run_id        -> research_runs(id)
--   recommendations.opportunity_id -> oportunidade canônica (ver data contract, §IDs canônicos)
--   interactions.campaign_id       -> entidade de campanha ainda não definida no baseline
-- =====================================================================================
