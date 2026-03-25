class OraculoDomainException(Exception):
    """Exceção base para todo o domínio do Oráculo."""
    pass

class UserNotActiveError(OraculoDomainException):
    """Lançada quando um usuário não possui status de acesso permitido."""
    pass

class InvalidMatriculaError(OraculoDomainException):
    """Lançada quando a matrícula foge do padrão estabelecido."""
    pass

