from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

# Modelo de Usuário
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    
    # Relacionamentos
    turmas = db.relationship('Turma', backref='professor', lazy=True)
    aulas = db.relationship('Aula', backref='professor_criador', lazy=True)
    professores_adjuntos = db.relationship('ProfessorAdjunto', backref='titular', lazy=True)

    def to_dict(self):
        return {
            "email": self.email,
            "nome": self.nome,
            "turmas": [t.to_dict() for t in self.turmas],
            # MENTORIA: Incluímos os professores adjuntos para não quebrar vínculos ao restaurar
            "professores_adjuntos": [p.to_dict() for p in self.professores_adjuntos]
        }

# Modelo de Professor Adjunto
class ProfessorAdjunto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    
    def to_dict(self):
        return {"id": self.id, "nome": self.nome}

# Modelo de Turma
class Turma(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    nome = db.Column(db.String(100), nullable=False) 
    codigo_completo = db.Column(db.String(150))      
    unidade_curricular = db.Column(db.String(200))   
    link_diario = db.Column(db.String(300))          
    ativa = db.Column(db.Boolean, default=True)      
    
    # Cascade all garante que se deletar a turma, as aulas somem (evita aulas órfãs)
    aulas = db.relationship('Aula', backref='turma', cascade="all, delete-orphan", lazy=True)

    def to_dict(self):
        return {
            "nome": self.nome,
            "codigo_completo": self.codigo_completo,
            "unidade_curricular": self.unidade_curricular,
            "link_diario": self.link_diario,
            "ativa": self.ativa,
            "aulas": [a.to_dict() for a in self.aulas]
        }

# Modelo de Aula
class Aula(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    turma_id = db.Column(db.Integer, db.ForeignKey('turma.id'), nullable=False)
    professor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    ministrante_id = db.Column(db.Integer, db.ForeignKey('professor_adjunto.id'), nullable=True)
    ministrante_rel = db.relationship('ProfessorAdjunto', backref='aulas_ministradas')

    titulo = db.Column(db.String(200), nullable=False)
    data = db.Column(db.Date, nullable=False)
    turno = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='Planejar')
    numero_aula = db.Column(db.Integer)
    
    sala = db.Column(db.String(50))
    unidade_predio = db.Column(db.String(50))
    bloco_estudo = db.Column(db.String(50))
    
    descricao = db.Column(db.Text)
    observacoes = db.Column(db.String(200))
    link_arquivos = db.Column(db.String(200))

    def to_json(self):
        # Usado para o Modal de Edição (Frontend) - AJAX
        return {
            'id': self.id,
            'turma_id': self.turma_id,  
            'titulo': self.titulo,
            'data': self.data.strftime('%Y-%m-%d'),
            'turno': self.turno,
            'status': self.status,
            'sala': self.sala,
            'unidade_predio': self.unidade_predio,
            'bloco_estudo': self.bloco_estudo,
            'numero_aula': self.numero_aula,
            'descricao': self.descricao,
            'link_arquivos': self.link_arquivos,
            'observacoes': self.observacoes,
            'turma_nome': self.turma.nome,
            'codigo': self.turma.codigo_completo,
            'ministrante_id': self.ministrante_id or 'me'
        }

    def to_dict(self):
        # MENTORIA: CORREÇÃO CRÍTICA
        # Agora exportamos TODOS os campos para garantir integridade do backup.
        # Adicionei também chaves compatíveis com o método de restore.
        return {
            "titulo": self.titulo,
            "data": self.data.strftime('%Y-%m-%d'),
            "turno": self.turno,
            "status": self.status,
            "descricao": self.descricao,
            "sala": self.sala,
            "unidade_predio": self.unidade_predio, # Novo
            "bloco_estudo": self.bloco_estudo,     # Novo
            "numero_aula": self.numero_aula,       # Novo
            "observacoes": self.observacoes,       # Novo
            "link_arquivos": self.link_arquivos,   # Novo
            "ministrante_nome": self.ministrante_rel.nome if self.ministrante_rel else None # Opcional, ajuda na auditoria
        }
