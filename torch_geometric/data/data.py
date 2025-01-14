import re
import warnings
import torch
from torch_sparse import coalesce
from torch_geometric.utils import (contains_isolated_nodes,
                                   contains_self_loops, is_undirected)

from ..utils.num_nodes import maybe_num_nodes

__num_nodes_warn_msg__ = (
    'The number of nodes in your data object can only be inferred by its {} '
    'indices, and hence may result in unexpected batch-wise behavior, e.g., '
    'in case there exists isolated nodes. Please consider explicitly setting '
    'the number of nodes for this data object by assigning it to '
    'data.num_nodes.')


def size_repr(value):
    if torch.is_tensor(value):
        return list(value.size())
    elif isinstance(value, int) or isinstance(value, float):
        return [1]
    else:
        return value


class Data(object):
    r"""A plain old python object modeling a single graph with various
    (optional) attributes:

    Args:
        x (Tensor, optional): Node feature matrix with shape :obj:`[num_nodes,
            num_node_features]`. (default: :obj:`None`)
        edge_index (LongTensor, optional): Graph connectivity in COO format
            with shape :obj:`[2, num_edges]`. (default: :obj:`None`)
        edge_attr (Tensor, optional): Edge feature matrix with shape
            :obj:`[num_edges, num_edge_features]`. (default: :obj:`None`)
        y (Tensor, optional): Graph or node targets with arbitrary shape.
            (default: :obj:`None`)
        pos (Tensor, optional): Node position matrix with shape
            :obj:`[num_nodes, num_dimensions]`. (default: :obj:`None`)
        norm (Tensor, optional): Normal vector matrix with shape
            :obj:`[num_nodes, num_dimensions]`. (default: :obj:`None`)
        face (LongTensor, optional): Face adjacency matrix with shape
            :obj:`[3, num_faces]`. (default: :obj:`None`)

    The data object is not restricted to these attributes and can be extented
    by any other additional data.

    Example::

        data = Data(x=x, edge_index=edge_index)
        data.train_idx = torch.tensor([...], dtype=torch.long)
        data.test_mask = torch.tensor([...], dtype=torch.uint8)
    """

    def __init__(self,
                 x=None,
                 edge_index=None,
                 edge_attr=None,
                 y=None,
                 pos=None,
                 norm=None,
                 face=None,
                 **kwargs):
        self.x = x
        self.edge_index = edge_index
        self.edge_attr = edge_attr
        self.y = y
        self.pos = pos
        self.norm = norm
        self.face = face
        for key, item in kwargs.items():
            self[key] = item

    @staticmethod
    def from_dict(dictionary):
        r"""Creates a data object from a python dictionary."""
        data = Data()
        for key, item in dictionary.items():
            data[key] = item
        return data

    def __getitem__(self, key):
        r"""Gets the data of the attribute :obj:`key`."""
        return getattr(self, key)

    def __setitem__(self, key, value):
        """Sets the attribute :obj:`key` to :obj:`value`."""
        setattr(self, key, value)

    @property
    def keys(self):
        r"""Returns all names of graph attributes."""
        keys = [key for key in self.__dict__.keys() if self[key] is not None]
        if '__num_nodes__' in keys:
            keys.remove('__num_nodes__')
        return keys

    def __len__(self):
        r"""Returns the number of all present attributes."""
        return len(self.keys)

    def __contains__(self, key):
        r"""Returns :obj:`True`, if the attribute :obj:`key` is present in the
        data."""
        return key in self.keys

    def __iter__(self):
        r"""Iterates over all present attributes in the data, yielding their
        attribute names and content."""
        for key in sorted(self.keys):
            yield key, self[key]

    def __call__(self, *keys):
        r"""Iterates over all attributes :obj:`*keys` in the data, yielding
        their attribute names and content.
        If :obj:`*keys` is not given this method will iterative over all
        present attributes."""
        for key in sorted(self.keys) if not keys else keys:
            if key in self:
                yield key, self[key]

    def __cat_dim__(self, key, value):
        r"""Returns the dimension for which :obj:`value` of attribute
        :obj:`key` will get concatenated when creating batches.

        .. note::

            This method is for internal use only, and should only be overridden
            if the batch concatenation process is corrupted for a specific data
            attribute.
        """
        # `*index*` and `*face*` should be concatenated in the last dimension,
        # everything else in the first dimension.
        return -1 if bool(re.search('(index|face)', key)) else 0

    def __cumsum__(self, key, value):
        r"""If :obj:`True`, :obj:`value` of attribute :obj:`key` is
        cumulatively summed up when creating batches.

        .. note::

            This method is for internal use only, and should only be overridden
            if the batch concatenation process is corrupted for a specific data
            attribute.
        """
        return bool(re.search('(index|face)', key))

    @property
    def num_nodes(self):
        r"""Returns or sets the number of nodes in the graph.

        .. note::
            The number of nodes in your data object is typically automatically
            inferred, *e.g.*, when node features :obj:`x` are present.
            In some cases however, a graph may only be given by its edge
            indices :obj:`edge_index`.
            PyTorch Geometric then *guesses* the number of nodes
            according to :obj:`edge_index.max().item() + 1`, but in case there
            exists isolated nodes, this number has not to be correct and can
            therefore result in unexpected batch-wise behavior.
            Thus, we recommend to set the number of nodes in your data object
            explicitly via :obj:`data.num_nodes = ...`.
            You will be given a warning that requests you to do so.
        """
        if hasattr(self, '__num_nodes__') and self.__num_nodes__ is not None:
            return self.__num_nodes__
        for key, item in self('x', 'pos', 'norm', 'batch'):
            return item.size(self.__cat_dim__(key, item))
        if self.face is not None:
            warnings.warn(__num_nodes_warn_msg__.format('face'))
            return maybe_num_nodes(self.face)
        if self.edge_index is not None:
            warnings.warn(__num_nodes_warn_msg__.format('edge'))
            return maybe_num_nodes(self.edge_index)
        return None

    @num_nodes.setter
    def num_nodes(self, num_nodes):
        self.__num_nodes__ = num_nodes

    @property
    def num_edges(self):
        r"""Returns the number of edges in the graph."""
        for key, item in self('edge_index', 'edge_attr'):
            return item.size(self.__cat_dim__(key, item))
        return None

    @property
    def num_faces(self):
        r"""Returns the number of faces in the mesh."""
        if self.face is not None:
            return self.face.size(self.__cat_dim__('face', self.face))
        return None

    @property
    def num_node_features(self):
        r"""Returns the number of features per node in the graph."""
        return 1 if self.x.dim() == 1 else self.x.size(1)

    @property
    def num_edge_features(self):
        r"""Returns the number of features per edge in the graph."""
        return 1 if self.edge_attr.dim() == 1 else self.edge_attr.size(1)

    @property
    def num_features(self):
        r"""Alias for :py:attr:`~num_node_features`."""
        return self.num_node_features

    def is_coalesced(self):
        r"""Returns :obj:`True`, if edge indices are ordered and do not contain
        duplicate entries."""
        edge_index, _ = coalesce(self.edge_index, None, self.num_nodes,
                                 self.num_nodes)
        return self.edge_index.numel() == edge_index.numel() and (
            self.edge_index != edge_index).sum().item() == 0

    def coalesce(self):
        r""""Orders and removes duplicated entries from edge indices."""
        self.edge_index, self.edge_attr = coalesce(
            self.edge_index, self.edge_attr, self.num_nodes, self.num_nodes)
        return self

    def contains_isolated_nodes(self):
        r"""Returns :obj:`True`, if the graph does not contain isolated
        nodes."""
        return contains_isolated_nodes(self.edge_index, self.num_nodes)

    def contains_self_loops(self):
        """Returns :obj:`True`, if the graph does not contain self-loops."""
        return contains_self_loops(self.edge_index)

    def is_undirected(self):
        r"""Returns :obj:`True`, if graph edges are undirected."""
        return is_undirected(self.edge_index, self.num_nodes)

    def is_directed(self):
        r"""Returns :obj:`True`, if graph edges are directed."""
        return not self.is_undirected()

    def apply(self, func, *keys):
        r"""Applies the function :obj:`func` to all tensor attributes
        :obj:`*keys`. If :obj:`*keys` is not given, :obj:`func` is applied to
        all present attributes.
        """
        for key, item in self(*keys):
            if torch.is_tensor(item):
                self[key] = func(item)
        return self

    def contiguous(self, *keys):
        r"""Ensures a contiguous memory layout for all attributes :obj:`*keys`.
        If :obj:`*keys` is not given, all present attributes are ensured to
        have a contiguous memory layout."""
        return self.apply(lambda x: x.contiguous(), *keys)

    def to(self, device, *keys):
        r"""Performs tensor dtype and/or device conversion to all attributes
        :obj:`*keys`.
        If :obj:`*keys` is not given, the conversion is applied to all present
        attributes."""
        return self.apply(lambda x: x.to(device), *keys)

    def clone(self):
        return self.__class__.from_dict({k: v.clone() for k, v in self})

    def __repr__(self):
        info = ['{}={}'.format(key, size_repr(item)) for key, item in self]
        return '{}({})'.format(self.__class__.__name__, ', '.join(info))
